"""Chat models over OpenAI-compatible APIs (Groq, OpenRouter, ...) plus primary/fallback."""

import json
import logging
import time
from collections.abc import Sequence
from typing import Any, cast

import openai
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from roost.llm.errors import LLMError, LLMRequestError, LLMResponseError, LLMUnavailableError
from roost.llm.providers import (
    PROVIDERS,
    LLMSettings,
    ProviderSpec,
    get_llm_settings,
    parse_model_ref,
)
from roost.llm.types import (
    ChatMessage,
    ChatModel,
    ChatResult,
    TextSink,
    ToolCall,
    ToolSpec,
    Usage,
)
from roost.observability import turns
from roost.observability.tracing import set_attributes, span

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


def _call_attributes(call: turns.ModelCall, *, outcome: str) -> dict[str, object]:
    return {
        "gen_ai.usage.input_tokens": call.input_tokens,
        "gen_ai.usage.output_tokens": call.output_tokens,
        "roost.llm.purpose": call.purpose,
        "roost.llm.fallback_position": call.fallback_position,
        "roost.llm.cost_usd": float(call.cost_usd),
        "roost.llm.outcome": outcome,
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
        on_text: TextSink | None = None,
    ) -> ChatResult:
        # One span per call, with the model, tokens and estimated cost; never the messages.
        with span(
            "llm.chat",
            {"gen_ai.system": self._provider.key, "gen_ai.request.model": self._model},
        ) as current:
            started = time.perf_counter()
            try:
                if on_text is None:
                    result = self._call(messages, tools, temperature, max_tokens, started)
                else:
                    result = self._stream(
                        messages, tools, temperature, max_tokens, started, on_text
                    )
            except LLMError as error:
                elapsed = (time.perf_counter() - started) * 1000
                call = turns.record_model_call(self.name, 0, 0, elapsed, ok=False)
                set_attributes(current, _call_attributes(call, outcome=type(error).__name__))
                raise
            call = turns.record_model_call(
                self.name,
                result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.latency_ms,
                ok=True,
            )
            set_attributes(
                current,
                _call_attributes(call, outcome="ok")
                | {"gen_ai.response.finish_reasons": (result.finish_reason or "unknown",)},
            )
            return result

    def _call(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        started: float,
    ) -> ChatResult:
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
            # OpenRouter reports some upstream failures as HTTP 200 with an "error" object
            # and no choices. That is a provider outage, not a bad answer: fall back.
            detail = (completion.model_extra or {}).get("error")
            raise LLMUnavailableError(f"{self.name}: response had no choices ({detail})")
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

    def _stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        started: float,
        on_text: TextSink,
    ) -> ChatResult:
        """The same call, streamed: text goes to `on_text` as it arrives, and the pieces are
        put back together into the ChatResult a normal call returns (tool calls included)."""
        content: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage = Usage()
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=[_to_openai_message(m) for m in messages],
                tools=[_to_openai_tool(t) for t in tools] if tools else openai.omit,
                temperature=temperature,
                max_tokens=max_tokens if max_tokens is not None else openai.omit,
                stream=True,
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                if chunk.usage is not None:
                    usage = Usage(
                        prompt_tokens=chunk.usage.prompt_tokens,
                        completion_tokens=chunk.usage.completion_tokens,
                    )
                if not chunk.choices:
                    detail = (chunk.model_extra or {}).get("error")
                    if detail:  # OpenRouter: an upstream failure inside the stream
                        raise LLMUnavailableError(f"{self.name}: stream error ({detail})")
                    continue
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                if delta.content:
                    content.append(delta.content)
                    on_text.text(delta.content)
                for piece in delta.tool_calls or []:
                    call = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                    if piece.id:
                        call["id"] = piece.id
                    if piece.function is not None:
                        call["name"] += piece.function.name or ""
                        call["arguments"] += piece.function.arguments or ""
        except _TRANSIENT as exc:
            raise LLMUnavailableError(f"{self.name}: {type(exc).__name__}") from exc
        except openai.APIStatusError as exc:
            raise LLMRequestError(f"{self.name}: HTTP {exc.status_code}") from exc
        if finish_reason is None and not content and not calls:
            raise LLMUnavailableError(f"{self.name}: the stream ended without an answer")
        return ChatResult(
            message=ChatMessage(
                role="assistant",
                content="".join(content) or None,
                tool_calls=tuple(
                    ToolCall(
                        id=call["id"],
                        name=call["name"],
                        arguments=_parse_arguments(call["name"], call["arguments"]),
                    )
                    for _, call in sorted(calls.items())
                ),
            ),
            model=self.name,
            finish_reason=finish_reason,
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class FallbackChatModel:
    """Try each model in order; move to the next only when one is unavailable.

    Only transient failures fall through. A bad request or bad key is raised at once,
    because a fallback would hide a bug that needs fixing.
    """

    def __init__(self, primary: ChatModel, *fallbacks: ChatModel) -> None:
        if not fallbacks:
            raise ValueError("FallbackChatModel needs at least one fallback model")
        self._models = (primary, *fallbacks)

    @property
    def name(self) -> str:
        return " -> ".join(model.name for model in self._models)

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        on_text: TextSink | None = None,
    ) -> ChatResult:
        last_error: LLMUnavailableError | None = None
        for position, model in enumerate(self._models):
            try:
                with turns.fallback_position(position):
                    if on_text is None:
                        return model.chat(
                            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
                        )
                    return model.chat(
                        messages,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        on_text=on_text,
                    )
            except LLMUnavailableError as exc:
                logger.warning("%s unavailable (%s); trying the next model", model.name, exc)
                last_error = exc
                if on_text is not None:
                    on_text.reset()  # a half-written answer from a failed model is dropped
        logger.error("Every model in %s is unavailable", self.name)
        raise last_error or LLMUnavailableError(f"{self.name}: no model answered")


def build_chat_model(refs: Sequence[str], *, settings: LLMSettings | None = None) -> ChatModel:
    """A model, or a fallback chain, from refs like "groq:openai/gpt-oss-120b"."""
    if not refs:
        raise LLMRequestError("no models configured")
    models = [
        OpenAICompatibleChatModel.create(*parse_model_ref(ref), settings=settings) for ref in refs
    ]
    if len(models) == 1:
        return models[0]
    return FallbackChatModel(models[0], *models[1:])
