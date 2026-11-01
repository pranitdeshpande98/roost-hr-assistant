"""Tests for roost.agent.state."""

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from roost.agent.state import AgentState
from roost.core.enums import Channel, Language
from roost.llm.types import ChatMessage


def test_defaults_for_a_new_conversation() -> None:
    state = AgentState(language=Language.VI, channel=Channel.KIOSK)

    assert state.messages == []
    assert state.employee_id is None
    assert state.tool_rounds == 0


def test_language_and_channel_are_required() -> None:
    with pytest.raises(ValidationError):
        AgentState()  # type: ignore[call-arg]


def test_unknown_channel_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentState(language=Language.EN, channel="email")


def test_graph_appends_messages_and_replaces_other_fields() -> None:
    def reply(state: AgentState) -> dict[str, object]:
        return {
            "messages": [ChatMessage(role="assistant", content="Hola")],
            "tool_rounds": state.tool_rounds + 1,
        }

    graph = StateGraph(AgentState)
    graph.add_node("reply", reply)
    graph.add_edge(START, "reply")
    graph.add_edge("reply", END)
    start = AgentState(
        messages=[ChatMessage(role="user", content="Hi")],
        language=Language.ES,
        channel=Channel.TEAMS,
    )

    result = graph.compile().invoke(start)

    assert [m.content for m in result["messages"]] == ["Hi", "Hola"]  # appended
    assert result["tool_rounds"] == 1  # replaced
    assert result["language"] == Language.ES  # untouched fields carry through
