"""Tests for roost.llm.client, against a mocked HTTP transport (no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest

from roost.llm.client import OpenAICompatibleChatModel
from roost.llm.errors import LLMRequestError, LLMResponseError, LLMUnavailableError
from roost.llm.providers import PROVIDERS, LLMSettings
from roost.llm.types import ChatMessage, ToolCall, ToolSpec

Handler = Callable[[httpx2.Request], httpx2.Response]

LOOKUP_TOOL = ToolSpec(
    name="search_handbook",
    description="Search the employee handbook.",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)


def _completion(message: dict[str, Any], finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }


def _model(handler: Handler) -> OpenAICompatibleChatModel:
    client = openai.OpenAI(
        api_key="test-key",
        base_url=PROVIDERS["groq"].base_url,
        max_retries=0,  # tests must not sleep through SDK backoff
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    return OpenAICompatibleChatModel(PROVIDERS["groq"], "test-model", client=client)


def _reply_with(body: dict[str, Any], seen: list[dict[str, Any]] | None = None) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(200, json=body)

    return handler


def test_returns_text_reply_with_usage() -> None:
    model = _model(_reply_with(_completion({"role": "assistant", "content": "Hola"})))

    result = model.chat([ChatMessage(role="user", content="Hi")])

    assert result.message.content == "Hola"
    assert result.message.tool_calls == ()
    assert result.model == "groq/test-model"
    assert result.finish_reason == "stop"
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (12, 5)
    assert result.latency_ms >= 0


def test_parses_tool_call_arguments() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search_handbook", "arguments": '{"query": "vacation"}'},
            }
        ],
    }
    model = _model(_reply_with(_completion(message, "tool_calls")))

    result = model.chat([ChatMessage(role="user", content="vacation?")], tools=[LOOKUP_TOOL])

    assert result.message.tool_calls == (
        ToolCall(id="call_1", name="search_handbook", arguments={"query": "vacation"}),
    )


def test_sends_messages_tools_and_settings_in_openai_format() -> None:
    seen: list[dict[str, Any]] = []
    model = _model(_reply_with(_completion({"role": "assistant", "content": "ok"}), seen))
    history = [
        ChatMessage(role="system", content="You are Roost."),
        ChatMessage(
            role="assistant",
            tool_calls=(ToolCall(id="c1", name="search_handbook", arguments={"query": "pto"}),),
        ),
        ChatMessage(role="tool", content="6.1 ...", tool_call_id="c1"),
    ]

    model.chat(history, tools=[LOOKUP_TOOL], temperature=0.2, max_tokens=300)

    body = seen[0]
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 300
    assert body["tools"][0]["function"]["name"] == "search_handbook"
    assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"query": "pto"}'
    assert body["messages"][2] == {"role": "tool", "content": "6.1 ...", "tool_call_id": "c1"}


def test_omits_tools_and_max_tokens_when_not_given() -> None:
    seen: list[dict[str, Any]] = []
    model = _model(_reply_with(_completion({"role": "assistant", "content": "ok"}), seen))

    model.chat([ChatMessage(role="user", content="Hi")])

    assert "tools" not in seen[0]
    assert "max_tokens" not in seen[0]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_statuses_raise_unavailable(status: int) -> None:
    model = _model(lambda _: httpx2.Response(status, json={"error": {"message": "busy"}}))

    with pytest.raises(LLMUnavailableError, match="groq/test-model"):
        model.chat([ChatMessage(role="user", content="Hi")])


def test_connection_failure_raises_unavailable() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    with pytest.raises(LLMUnavailableError):
        _model(refuse).chat([ChatMessage(role="user", content="Hi")])


@pytest.mark.parametrize("status", [400, 401, 404])
def test_client_errors_raise_request_error(status: int) -> None:
    model = _model(lambda _: httpx2.Response(status, json={"error": {"message": "no"}}))

    with pytest.raises(LLMRequestError, match=f"HTTP {status}"):
        model.chat([ChatMessage(role="user", content="Hi")])


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
def test_bad_tool_arguments_raise_response_error(arguments: str) -> None:
    message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "search_handbook", "arguments": arguments},
            }
        ],
    }
    model = _model(_reply_with(_completion(message, "tool_calls")))

    with pytest.raises(LLMResponseError, match="search_handbook"):
        model.chat([ChatMessage(role="user", content="Hi")])


def test_create_rejects_unknown_provider() -> None:
    with pytest.raises(LLMRequestError, match="unknown provider"):
        OpenAICompatibleChatModel.create("nope", "m", settings=LLMSettings())


def test_create_requires_the_provider_key() -> None:
    settings = LLMSettings(_env_file=None, groq_api_key=None)

    with pytest.raises(LLMRequestError, match="GROQ_API_KEY"):
        OpenAICompatibleChatModel.create("groq", "m", settings=settings)


def test_create_builds_a_named_model() -> None:
    settings = LLMSettings(_env_file=None, openrouter_api_key="sk-test")

    model = OpenAICompatibleChatModel.create("openrouter", "some/model", settings=settings)

    assert model.name == "openrouter/some/model"
