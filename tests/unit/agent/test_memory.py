"""Tests for roost.agent.memory that need no database."""

import uuid

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from roost.agent.memory import SAFE_CHECKPOINT_TYPES, psycopg_url
from roost.agent.state import AgentState
from roost.core.enums import Channel, Language
from roost.guardrails.types import Category
from roost.llm.types import ChatMessage, ToolCall


def test_psycopg_url_drops_the_sqlalchemy_driver_name() -> None:
    url = "postgresql+psycopg://roost:pw@127.0.0.1:5433/roost"

    assert psycopg_url(url) == "postgresql://roost:pw@127.0.0.1:5433/roost"


def test_plain_urls_are_unchanged() -> None:
    assert psycopg_url("postgresql://h/db") == "postgresql://h/db"


def test_every_type_stored_in_agent_state_is_allowlisted() -> None:
    assert set(SAFE_CHECKPOINT_TYPES) == {
        ("roost.llm.types", "ChatMessage"),
        ("roost.llm.types", "ToolCall"),
        ("roost.core.enums", "Language"),
        ("roost.core.enums", "Channel"),
        ("roost.guardrails.types", "Category"),
    }


def test_a_full_state_survives_the_production_serializer() -> None:
    # A type missing from the allowlist comes back as plain data (with a warning), not as
    # itself: round-trip a state that uses every field so a new field cannot slip through.
    serde = JsonPlusSerializer(allowed_msgpack_modules=SAFE_CHECKPOINT_TYPES)
    state = AgentState(
        messages=[
            ChatMessage(role="user", content="Book Monday"),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="c1", name="request_vacation", arguments={})],
            ),
        ],
        language=Language.VI,
        channel=Channel.TEAMS,
        employee_id=uuid.uuid4(),
        tool_rounds=1,
        reply="ok",
        guardrail=Category.OWN_DATA,
    )
    values = dict(state)

    restored = serde.loads_typed(serde.dumps_typed(values))

    assert restored == values
    assert type(restored["guardrail"]) is Category
    assert type(restored["messages"][1].tool_calls[0]) is ToolCall
