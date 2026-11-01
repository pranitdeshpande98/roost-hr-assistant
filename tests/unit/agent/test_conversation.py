"""Tests for roost.agent.conversation: memory across turns, with an in-memory checkpointer."""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from roost.agent.conversation import ask
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.core.enums import Channel, Language
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall, ToolSpec


class LookupTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="lookup", description="Look up.", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        return "[§6.1] Ten days."


def _agent(model: ScriptedChatModel, **kwargs: Any) -> Any:
    return build_graph(
        model,
        ToolRegistry([LookupTool()]),
        system_prompt=lambda state, today: "sys",
        messages=default_messages(),
        checkpointer=InMemorySaver(),
        **kwargs,
    )


def _call(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="lookup", arguments={})


EN: dict[str, Any] = {"language": Language.EN, "channel": Channel.KIOSK}


def test_second_turn_sees_the_first() -> None:
    model = ScriptedChatModel([text_reply("Ten days."), text_reply("Two days.")])
    agent = _agent(model)

    ask(agent, "t1", "How many vacation days?", **EN)
    reply = ask(agent, "t1", "And sick days?", **EN)

    assert reply.startswith("Two days.")
    sent = [(m.role, m.content) for m in model.requests[1].messages[1:]]
    assert sent == [
        ("user", "How many vacation days?"),
        ("assistant", "Ten days."),
        ("user", "And sick days?"),
    ]


def test_threads_are_isolated() -> None:
    model = ScriptedChatModel([text_reply("A"), text_reply("B")])
    agent = _agent(model)

    ask(agent, "employee-1", "secret question", **EN)
    ask(agent, "employee-2", "hello", **EN)

    contents = [m.content for m in model.requests[1].messages]
    assert "secret question" not in contents


def test_tool_rounds_reset_every_turn() -> None:
    model = ScriptedChatModel(
        [tool_reply(_call("c1")), text_reply("one"), tool_reply(_call("c2")), text_reply("two")]
    )
    agent = _agent(model, max_tool_rounds=1)

    ask(agent, "t1", "first", **EN)
    reply = ask(agent, "t1", "second", **EN)

    # Turn 2 was offered tools again and used them, instead of inheriting turn 1's count.
    assert model.requests[2].tools is not None
    assert reply.startswith("two")


def test_language_can_change_between_turns() -> None:
    model = ScriptedChatModel([text_reply("Ten days."), text_reply("Diez días.")])
    agent = _agent(model)

    ask(agent, "t1", "How many days?", **EN)
    reply = ask(agent, "t1", "¿Cuántos días?", language=Language.ES, channel=Channel.TEAMS)

    assert default_messages().get("binding_notice", Language.ES) in reply
