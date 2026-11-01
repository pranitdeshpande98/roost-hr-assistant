"""Tests for roost.agent.tools."""

from datetime import date
from typing import Any

import pytest
from langgraph.errors import GraphInterrupt

from roost.agent.confirmation import ConfirmationReply
from roost.agent.tools import ToolContext, ToolRegistry
from roost.core.enums import Language
from roost.llm.types import ToolCall, ToolSpec

CONTEXT = ToolContext(
    employee_id=None,
    language=Language.EN,
    today=date(2026, 11, 2),
    confirm=ConfirmationReply.from_text,
)


class EchoTool:
    """Returns its 'text' argument; raises if asked to."""

    def __init__(self, name: str = "echo") -> None:
        self._spec = ToolSpec(name=name, description="Echo text.", parameters={"type": "object"})

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        if arguments.get("explode"):
            raise RuntimeError("database password is hunter2")
        if arguments.get("pause"):
            raise GraphInterrupt(())
        return str(arguments["text"])


def test_runs_the_named_tool_and_links_the_reply_to_the_call() -> None:
    registry = ToolRegistry([EchoTool()])

    message = registry.execute(ToolCall(id="c1", name="echo", arguments={"text": "hi"}), CONTEXT)

    assert message.role == "tool"
    assert message.content == "hi"
    assert message.tool_call_id == "c1"


def test_unknown_tool_becomes_an_error_message() -> None:
    message = ToolRegistry([EchoTool()]).execute(
        ToolCall(id="c1", name="nope", arguments={}), CONTEXT
    )

    assert message.content == "Error: unknown tool 'nope'."


def test_tool_failure_is_contained_and_details_are_not_leaked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = ToolRegistry([EchoTool()])

    message = registry.execute(ToolCall(id="c1", name="echo", arguments={"explode": True}), CONTEXT)

    assert message.content == "Error: the echo tool failed. Do not retry it."
    assert "hunter2" not in (message.content or "")  # internals stay out of the model
    assert "Tool echo failed" in caplog.text  # but are logged for us


def test_specs_lists_every_tool() -> None:
    registry = ToolRegistry([EchoTool("a"), EchoTool("b")])

    assert [spec.name for spec in registry.specs] == ["a", "b"]


def test_duplicate_tool_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate tool name: echo"):
        ToolRegistry([EchoTool(), EchoTool()])


def test_langgraph_pause_signals_are_not_swallowed() -> None:
    # A tool asking for confirmation raises GraphInterrupt; catching it would break the pause.
    registry = ToolRegistry([EchoTool()])

    with pytest.raises(GraphInterrupt):
        registry.execute(ToolCall(id="c1", name="echo", arguments={"pause": True}), CONTEXT)
