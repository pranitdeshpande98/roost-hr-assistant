"""Tests for roost.agent.graph.recent_turns."""

from roost.agent.graph import recent_turns
from roost.llm.types import ChatMessage, ToolCall


def _user(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def _assistant(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)


def test_short_history_is_unchanged() -> None:
    history = [_user("q1"), _assistant("a1")]

    assert recent_turns(history, 3) == history


def test_keeps_only_the_last_turns() -> None:
    history = [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2"), _user("q3")]

    assert [m.content for m in recent_turns(history, 2)] == ["q2", "a2", "q3"]


def test_never_splits_a_tool_call_from_its_result() -> None:
    call = ToolCall(id="c1", name="search_handbook", arguments={"query": "x"})
    history = [
        _user("q1"),
        _assistant("a1"),
        _user("q2"),
        ChatMessage(role="assistant", tool_calls=(call,)),
        ChatMessage(role="tool", content="result", tool_call_id="c1"),
        _assistant("a2"),
    ]

    kept = recent_turns(history, 1)

    assert kept[0].content == "q2"  # starts at the employee's message
    assert [m.role for m in kept] == ["user", "assistant", "tool", "assistant"]
