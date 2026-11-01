"""The handbook tool and the assembled agent, against the real handbook in Postgres."""

import hashlib
from collections.abc import Sequence
from contextlib import nullcontext
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.agent.confirmation import ConfirmationReply
from roost.agent.factory import build_agent
from roost.agent.messages import default_messages
from roost.agent.state import AgentState
from roost.agent.tools import ToolContext
from roost.core.enums import Channel, Language
from roost.db.models import ChunkEmbedding, HandbookChunk
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ChatMessage, ToolCall
from roost.rag.ingest import ingest_handbook
from roost.tools.handbook import HandbookSearchTool, database_search

pytestmark = pytest.mark.integration

HANDBOOK = Path(__file__).resolve().parents[2] / "data" / "raw" / "handbook" / "handbook-v1.0.md"
MODEL = "fake-embedder"


def _vector(text: str) -> list[float]:
    return [byte / 255 for byte in hashlib.sha256(text.encode("utf-8")).digest()[:8]]


class SectionNumberEmbedder:
    """Embeds a query that IS a section number onto that section's stored vector."""

    model_name = MODEL

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vector(t) for t in texts]

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vector(t) for t in texts]


@pytest.fixture
def handbook(db_session: Session) -> Session:
    ingest_handbook(
        db_session,
        markdown=HANDBOOK.read_text(encoding="utf-8"),
        source_filename=HANDBOOK.name,
        version_label="1.0",
        effective_date=date(2026, 11, 1),
        publish=True,
    )
    chunks = db_session.scalars(select(HandbookChunk)).all()
    db_session.add_all(
        ChunkEmbedding(chunk_id=c.id, model_name=MODEL, embedding=_vector(c.section_number))
        for c in chunks
    )
    db_session.flush()
    return db_session


def test_database_search_returns_real_section_text(handbook: Session) -> None:
    search = database_search(lambda: nullcontext(handbook), SectionNumberEmbedder(), top_k=2)

    sections = search("6.4")

    assert sections[0].section_number == "6.4"
    assert "notice" in sections[0].content.lower()
    assert len(sections) == 2


def test_tool_output_contains_the_cited_section(handbook: Session) -> None:
    tool = HandbookSearchTool(
        database_search(lambda: nullcontext(handbook), SectionNumberEmbedder())
    )

    context = ToolContext(
        employee_id=None,
        language=Language.EN,
        today=date(2026, 11, 2),
        confirm=ConfirmationReply.from_text,
    )
    result = tool.run({"query": "9.2"}, context)

    assert result.startswith("[§9.2] ")


def test_assembled_agent_answers_from_the_handbook(handbook: Session) -> None:
    model = ScriptedChatModel(
        [
            tool_reply(ToolCall(id="c1", name="search_handbook", arguments={"query": "6.4"})),
            text_reply("Pida sus vacaciones con dos semanas de anticipación [§6.4]."),
        ]
    )
    agent = build_agent(
        lambda: nullcontext(handbook), model=model, embedder=SectionNumberEmbedder()
    )

    result = agent.invoke(
        AgentState(
            messages=[
                ChatMessage(role="user", content="¿Con cuánta anticipación pido vacaciones?")
            ],
            language=Language.ES,
            channel=Channel.KIOSK,
        )
    )

    tool_message = result["messages"][2]
    assert tool_message.role == "tool"
    assert result["reply"].startswith("Pida sus vacaciones")
    assert result["reply"].endswith(default_messages().get("disclaimer", Language.ES))
    # The system prompt sent to the model asks for Spanish.
    assert "Reply in Spanish" in (model.requests[0].messages[0].content or "")
