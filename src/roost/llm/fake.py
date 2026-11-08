"""A scripted chat model for tests and offline runs. Never touches the network."""

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from roost.llm.types import ChatMessage, ChatResult, TextSink, ToolCall, ToolSpec, Usage
from roost.observability import turns


def text_reply(content: str, *, model: str = "fake/scripted") -> ChatResult:
    """A plain assistant answer."""
    return ChatResult(
        message=ChatMessage(role="assistant", content=content),
        model=model,
        finish_reason="stop",
        usage=Usage(),
        latency_ms=0.0,
    )


def tool_reply(*calls: ToolCall, model: str = "fake/scripted") -> ChatResult:
    """An assistant turn that asks for one or more tool calls."""
    return ChatResult(
        message=ChatMessage(role="assistant", tool_calls=calls),
        model=model,
        finish_reason="tool_calls",
        usage=Usage(),
        latency_ms=0.0,
    )


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Everything one call to `chat` received."""

    messages: list[ChatMessage]
    tools: list[ToolSpec] | None
    temperature: float
    max_tokens: int | None


class ScriptedChatModel:
    """Returns (or raises) queued items in order, and records every request it got."""

    def __init__(self, script: Iterable[ChatResult | Exception], *, name: str = "fake/scripted"):
        self._script: deque[ChatResult | Exception] = deque(script)
        self._name = name
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._name

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        on_text: TextSink | None = None,
    ) -> ChatResult:
        self.requests.append(ChatRequest(list(messages), tools, temperature, max_tokens))
        if not self._script:
            raise AssertionError(f"{self._name}: no scripted reply left")
        item = self._script.popleft()
        if isinstance(item, Exception):
            turns.record_model_call(self._name, 0, 0, 0.0, ok=False)
            raise item
        if on_text is not None and item.message.content:
            # Stream the scripted answer a word at a time, like a provider would.
            for word in re.findall(r"\S+\s*", item.message.content):
                on_text.text(word)
        usage = item.usage
        turns.record_model_call(
            item.model, usage.prompt_tokens, usage.completion_tokens, item.latency_ms, ok=True
        )
        return item
