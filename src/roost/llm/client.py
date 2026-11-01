"""Chat models over OpenAI-compatible APIs (Groq, OpenRouter, ...) plus primary/fallback."""

import json
import logging
import time
from typing import Any, cast

import openai
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from roost.llm.errors import LLMError, LLMRequestError, LLMResponseError, LLMUnavailableError
from roost.llm.providers import PROVIDERS, LLMSettings, ProviderSpec, get_llm_settings
from roost.llm.types import ChatMessage, ChatModel, ChatResult, ToolCall, ToolSpec, Usage

logger = logging.getLogger(__name__)

# Statuses worth trying another provider for. Everything else 4xx is a bug or config error.
_TRANSIENT = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def _to_openai_message(message: ChatMessage) -> ChatCompletionMessageParam:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return cast("ChatCompletionMessageParam", payload)


def _to_openai_tool(tool: ToolSpec) -> ChatCompletionToolParam:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _parse_arguments(name: str, raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"tool {name!r}: arguments are not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError(f"tool {name!r}: arguments must be a JSON object")
    return parsed


class OpenAICompatibleChatModel:
    """One model on one provider, spoken to through the OpenAI chat-completions API."""

    def __init__(
        self,
        provider: ProviderSpec,
        model: str,
        *,
        client: openai.OpenAI,
    ) -> None:
        self._provider = provider
        self._model = model
        self._client = client

    @classmethod
    def create(
        cls,
        provider: str,
        model: str,
        *,
        settings: LLMSettings | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 2,
    ) -> "OpenAICompatibleChatModel":
        """Build a client from the provider registry and API keys in settings."""
        if provider not in PROVIDERS:
            raise LLMRequestError(f"unknown provider {provider!r}; known: {sorted(PROVIDERS)}")
        spec = PROVIDERS[provider]
        key = (settings or get_llm_settings()).api_key_for(provider)
        client = openai.OpenAI(
            base_url=spec.base_url,
            api_key=key.get_secret_value(),
            timeout=timeout_s,
            max_retries=max_retries,  # SDK retries 429/5xx with backoff before we see an error
            default_headers=spec.default_headers or None,
        )
        return cls(spec, model, client=client)

    @property
    def name(self) -> str:
        return f"{self._provider.key}/{self._model}"

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=[_to_openai_message(m) for m in messages],
                tools=[_to_openai_tool(t) for t in tools] if tools else openai.omit,
                temperature=temperature,
                max_tokens=max_tokens if max_tokens is not None else openai.omit,
            )
        except _TRANSIENT as exc:
            raise LLMUnavailableError(f"{self.name}: {type(exc).__name__}") from exc
        except openai.APIStatusError as exc:
            raise LLMRequestError(f"{self.name}: HTTP {exc.status_code}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        if not completion.choices:
            raise LLMResponseError(f"{self.name}: response had no choices")
        choice = completion.choices[0]
        calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.name, call.function.arguments),
            )
            for call in choice.message.tool_calls or []
            if call.type == "function"
        )
        usage = completion.usage
        return ChatResult(
            message=ChatMessage(role="assistant", content=choice.message.content, tool_calls=calls),
            model=self.name,
            finish_reason=choice.finish_reason,
            usage=Usage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            ),
            latency_ms=latency_ms,
        )


class FallbackChatModel:
    """Use the primary model; if it is unavailable, answer with the fallback instead.

    Only transient failures fall back. A bad request or bad key is raised at once,
    because the fallback would hide a bug that needs fixing.
    """

    def __init__(self, primary: ChatModel, fallback: ChatModel) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def name(self) -> str:
        return f"{self._primary.name} -> {self._fallback.name}"

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        try:
            return self._primary.chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            )
        except LLMUnavailableError as exc:
            logger.warning(
                "Primary %s unavailable (%s); using %s",
                self._primary.name,
                exc,
                self._fallback.name,
            )
        try:
            return self._fallback.chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            )
        except LLMError:
            logger.exception("Fallback %s also failed", self._fallback.name)
            raise
