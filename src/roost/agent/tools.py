"""The contract every agent tool follows, and the registry that runs them."""

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from langgraph.errors import GraphBubbleUp

from roost.agent.confirmation import ConfirmationReply
from roost.core.enums import Language
from roost.llm.types import ChatMessage, ToolCall, ToolSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Facts a tool may rely on that the model must NOT be able to supply: who is asking,
    today's date, and a way to ask the employee to confirm."""

    employee_id: uuid.UUID | None
    language: Language
    today: date
    confirm: Callable[[str], ConfirmationReply]  # shows the text, returns the employee's answer


class Tool(Protocol):
    """Something the model can ask us to do: described by a spec, run with JSON args."""

    @property
    def spec(self) -> ToolSpec: ...

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str: ...


class ToolRegistry:
    """Looks tools up by name and turns each model tool call into a tool message."""

    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            name = tool.spec.name
            if name in self._tools:
                raise ValueError(f"duplicate tool name: {name}")
            self._tools[name] = tool

    @property
    def specs(self) -> list[ToolSpec]:
        """What to offer the model."""
        return [tool.spec for tool in self._tools.values()]

    def execute(self, call: ToolCall, context: ToolContext) -> ChatMessage:
        """Run one call. Failures become a short tool message, never an exception:
        the model can recover (retry, apologise), and internals are not leaked to it."""
        tool = self._tools.get(call.name)
        if tool is None:
            content = f"Error: unknown tool '{call.name}'."
        else:
            try:
                content = tool.run(call.arguments, context)
            except GraphBubbleUp:
                raise  # LangGraph's own signals (e.g. pausing for confirmation) must pass
            except Exception:
                logger.exception("Tool %s failed", call.name)
                content = f"Error: the {call.name} tool failed. Do not retry it."
        return ChatMessage(role="tool", content=content, tool_call_id=call.id)
