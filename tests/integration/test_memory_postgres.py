"""Conversation memory in real Postgres: survives closing and reopening the checkpointer."""

from typing import Any

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import Engine, text

from roost.agent.conversation import ask
from roost.agent.graph import build_graph
from roost.agent.memory import MEMORY_SCHEMA, open_checkpointer
from roost.agent.messages import default_messages
from roost.agent.state import AgentState
from roost.agent.tools import ToolRegistry
from roost.core.enums import Channel, Language
from roost.llm.fake import ScriptedChatModel, text_reply

pytestmark = pytest.mark.integration

EN: dict[str, Any] = {"language": Language.EN, "channel": Channel.KIOSK}


def _url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def test_migration_created_the_memory_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
            {"name": MEMORY_SCHEMA},
        ).scalar()

    assert found == 1


def test_conversation_survives_a_restart(engine: Engine) -> None:
    model = ScriptedChatModel([text_reply("Ten days."), text_reply("Two days.")])

    def agent_on(
        checkpointer: BaseCheckpointSaver[Any],
    ) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        return build_graph(
            model,
            ToolRegistry([]),
            system_prompt=lambda state, today: "sys",
            messages=default_messages(),
            checkpointer=checkpointer,
        )

    with open_checkpointer(_url(engine)) as first:
        ask(agent_on(first), "restart-test", "How many vacation days?", **EN)

    # A new connection, as if the app restarted between the two messages.
    with open_checkpointer(_url(engine)) as second:
        reply = ask(agent_on(second), "restart-test", "And sick days?", **EN)
        second.delete_thread("restart-test")  # leave no rows behind

    assert reply.startswith("Two days.")
    assert [m.content for m in model.requests[1].messages[1:]] == [
        "How many vacation days?",
        "Ten days.",
        "And sick days?",
    ]
