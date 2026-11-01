"""Tests for roost.llm.client.FallbackChatModel, using scripted models."""

import logging

import pytest

from roost.llm.client import FallbackChatModel
from roost.llm.errors import LLMRequestError, LLMUnavailableError
from roost.llm.fake import ScriptedChatModel, text_reply
from roost.llm.types import ChatMessage

HELLO = [ChatMessage(role="user", content="Hi")]


def test_uses_primary_when_it_answers() -> None:
    primary = ScriptedChatModel([text_reply("from primary")], name="p")
    fallback = ScriptedChatModel([], name="f")

    result = FallbackChatModel(primary, fallback).chat(HELLO)

    assert result.message.content == "from primary"
    assert fallback.requests == []


def test_falls_back_when_primary_is_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    primary = ScriptedChatModel([LLMUnavailableError("rate limited")], name="p")
    fallback = ScriptedChatModel([text_reply("from fallback", model="f")], name="f")

    with caplog.at_level(logging.WARNING):
        result = FallbackChatModel(primary, fallback).chat(HELLO, temperature=0.3)

    assert result.model == "f"
    assert fallback.requests[0].messages == HELLO
    assert fallback.requests[0].temperature == 0.3  # settings pass through unchanged
    assert "p unavailable" in caplog.text


def test_walks_the_chain_until_a_model_answers() -> None:
    first = ScriptedChatModel([LLMUnavailableError("429")], name="a")
    second = ScriptedChatModel([LLMUnavailableError("no choices")], name="b")
    third = ScriptedChatModel([text_reply("from c", model="c")], name="c")

    result = FallbackChatModel(first, second, third).chat(HELLO)

    assert result.model == "c"
    assert len(second.requests) == 1


def test_does_not_fall_back_on_request_errors() -> None:
    primary = ScriptedChatModel([LLMRequestError("HTTP 401")], name="p")
    fallback = ScriptedChatModel([text_reply("unused")], name="f")

    with pytest.raises(LLMRequestError):
        FallbackChatModel(primary, fallback).chat(HELLO)
    assert fallback.requests == []


def test_request_error_midway_stops_the_chain() -> None:
    first = ScriptedChatModel([LLMUnavailableError("429")], name="a")
    second = ScriptedChatModel([LLMRequestError("HTTP 404")], name="b")
    third = ScriptedChatModel([text_reply("unused")], name="c")

    with pytest.raises(LLMRequestError, match="404"):
        FallbackChatModel(first, second, third).chat(HELLO)
    assert third.requests == []


def test_raises_the_last_error_when_every_model_fails() -> None:
    primary = ScriptedChatModel([LLMUnavailableError("down")], name="p")
    fallback = ScriptedChatModel([LLMUnavailableError("also down")], name="f")

    with pytest.raises(LLMUnavailableError, match="also down"):
        FallbackChatModel(primary, fallback).chat(HELLO)


def test_needs_at_least_one_fallback() -> None:
    with pytest.raises(ValueError, match="at least one fallback"):
        FallbackChatModel(ScriptedChatModel([], name="p"))


def test_name_shows_the_whole_chain() -> None:
    models = [ScriptedChatModel([], name=n) for n in ("a", "b", "c")]

    assert FallbackChatModel(*models).name == "a -> b -> c"
