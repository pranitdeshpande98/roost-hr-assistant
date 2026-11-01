"""Tests for roost.llm.fake."""

import pytest

from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ChatMessage, ToolCall


def test_replays_script_in_order_and_records_requests() -> None:
    call = ToolCall(id="c1", name="search_handbook", arguments={"query": "pto"})
    model = ScriptedChatModel([tool_reply(call), text_reply("done")])

    first = model.chat([ChatMessage(role="user", content="a")], max_tokens=50)
    second = model.chat([ChatMessage(role="user", content="b")])

    assert first.message.tool_calls == (call,)
    assert first.finish_reason == "tool_calls"
    assert second.message.content == "done"
    assert [r.messages[0].content for r in model.requests] == ["a", "b"]
    assert model.requests[0].max_tokens == 50


def test_raises_scripted_exceptions() -> None:
    model = ScriptedChatModel([RuntimeError("boom")])

    with pytest.raises(RuntimeError, match="boom"):
        model.chat([])


def test_fails_loudly_when_script_runs_out() -> None:
    with pytest.raises(AssertionError, match="no scripted reply left"):
        ScriptedChatModel([]).chat([])
