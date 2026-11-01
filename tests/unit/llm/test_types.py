"""Tests for roost.llm.types and roost.llm.providers."""

import pytest
from pydantic import ValidationError

from roost.llm.errors import LLMRequestError
from roost.llm.providers import PROVIDERS, LLMSettings, parse_model_ref
from roost.llm.types import ChatMessage, ToolSpec


@pytest.mark.parametrize("name", ["search handbook", "", "x" * 65, "café"])
def test_tool_names_must_be_provider_safe(name: str) -> None:
    with pytest.raises(ValidationError):
        ToolSpec(name=name, description="d", parameters={"type": "object"})


def test_messages_are_immutable() -> None:
    message = ChatMessage(role="user", content="Hi")

    with pytest.raises(ValidationError):
        message.content = "changed"  # type: ignore[misc]


def test_unknown_roles_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role="admin", content="Hi")


def test_every_provider_uses_https() -> None:
    assert PROVIDERS
    assert all(spec.base_url.startswith("https://") for spec in PROVIDERS.values())


def test_api_key_for_rejects_empty_keys() -> None:
    settings = LLMSettings(_env_file=None, groq_api_key="")

    with pytest.raises(LLMRequestError, match="GROQ_API_KEY"):
        settings.api_key_for("groq")


def test_api_key_for_returns_secret_without_leaking_it() -> None:
    settings = LLMSettings(_env_file=None, groq_api_key="gsk-secret")

    key = settings.api_key_for("groq")

    assert key.get_secret_value() == "gsk-secret"
    assert "gsk-secret" not in repr(settings)


@pytest.mark.parametrize(
    ("ref", "parsed"),
    [
        ("groq:openai/gpt-oss-120b", ("groq", "openai/gpt-oss-120b")),
        # Split on the FIRST colon only: free-model ids contain one too.
        (
            " openrouter:nvidia/nemotron-3-ultra-550b-a55b:free ",
            ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        ),
    ],
)
def test_parse_model_ref(ref: str, parsed: tuple[str, str]) -> None:
    assert parse_model_ref(ref) == parsed


@pytest.mark.parametrize("ref", ["openai/gpt-oss-120b", "groq:", "nope:model", ""])
def test_parse_model_ref_rejects_bad_refs(ref: str) -> None:
    with pytest.raises(LLMRequestError, match="bad model ref"):
        parse_model_ref(ref)


def test_default_agent_chain_is_three_valid_refs() -> None:
    refs = LLMSettings(_env_file=None).agent_model_refs

    assert len(refs) == 3
    assert [parse_model_ref(r)[0] for r in refs] == ["groq", "groq", "openrouter"]


def test_agent_models_can_be_overridden() -> None:
    settings = LLMSettings(_env_file=None, agent_models=" groq:a , groq:b ,")

    assert settings.agent_model_refs == ["groq:a", "groq:b"]
