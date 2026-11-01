"""Provider-neutral chat data contracts. Agent code depends on these, never on a vendor SDK."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

MessageRole = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """A function the model asked us to run, with arguments already parsed from JSON."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(BaseModel):
    """One message in a conversation."""

    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None  # set on role="tool" messages: which call this answers


class ToolSpec(BaseModel):
    """A tool offered to the model; `parameters` is a JSON Schema object."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str
    parameters: dict[str, Any]


class Usage(BaseModel):
    """Token counts reported by the provider (0 when it reports none)."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0


class ChatResult(BaseModel):
    """The model's reply plus what we need for cost, latency and audit logging."""

    model_config = ConfigDict(frozen=True)

    message: ChatMessage
    model: str  # "provider/model" that actually answered (matters after a fallback)
    finish_reason: str | None
    usage: Usage
    latency_ms: float


class ChatModel(Protocol):
    """Anything that can answer a conversation: a real provider, a fallback pair, a fake."""

    @property
    def name(self) -> str: ...

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult: ...
