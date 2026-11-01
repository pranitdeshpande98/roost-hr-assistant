"""Tests for roost.agent.messages."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from roost.agent.messages import compose_reply, default_messages, load_messages
from roost.core.enums import Language

MESSAGES = default_messages()


def test_repository_message_file_is_complete() -> None:
    for language in Language:
        assert MESSAGES.get("disclaimer", language)
        assert MESSAGES.get("no_answer", language)


def test_binding_notice_only_for_translated_replies() -> None:
    assert MESSAGES.get("binding_notice", Language.EN) == ""
    assert MESSAGES.get("binding_notice", Language.ES)
    assert MESSAGES.get("binding_notice", Language.VI)


def test_english_reply_is_answer_then_disclaimer() -> None:
    reply = compose_reply("Ten days [§6.1].", Language.EN, MESSAGES)

    assert reply == f"Ten days [§6.1].\n\n{MESSAGES.get('disclaimer', Language.EN)}"


@pytest.mark.parametrize("language", [Language.ES, Language.VI])
def test_translated_reply_adds_the_binding_notice(language: Language) -> None:
    reply = compose_reply("…", language, MESSAGES)

    assert reply.split("\n\n") == [
        "…",
        MESSAGES.get("binding_notice", language),
        MESSAGES.get("disclaimer", language),
    ]


@pytest.mark.parametrize("answer", [None, "", "   "])
def test_missing_answer_uses_the_fixed_fallback(answer: str | None) -> None:
    reply = compose_reply(answer, Language.VI, MESSAGES)

    assert reply.startswith(MESSAGES.get("no_answer", Language.VI))
    assert reply.endswith(MESSAGES.get("disclaimer", Language.VI))  # FR-04: always


KEYS = (
    "disclaimer",
    "binding_notice",
    "no_answer",
    "confirm_submit",
    "confirm_change",
    "confirm_cancel",
    "full_day",
    "half_day",
)


def _write_catalog(path: Path, **overrides: str) -> Path:
    full = "{en: a, es: b, vi: c}"
    lines = [f"{key}: {overrides.get(key, full)}" for key in KEYS]
    lines += [f"{key}: {value}" for key, value in overrides.items() if key not in KEYS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_message_missing_a_language_fails_at_load(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path / "m.yaml", disclaimer="{en: a, es: b}")

    with pytest.raises(ValidationError, match="missing languages: vi"):
        load_messages(path)


def test_unknown_message_keys_fail_at_load(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path / "m.yaml", typo_key="{en: a, es: b, vi: c}")

    with pytest.raises(ValidationError, match="typo_key"):
        load_messages(path)


def test_a_translation_with_a_different_placeholder_fails_at_load(tmp_path: Path) -> None:
    broken = "{en: 'Cancel {reference}?', es: '¿Cancelar {referencia}?', vi: 'Hủy {reference}?'}"
    path = _write_catalog(tmp_path / "m.yaml", confirm_cancel=broken)

    with pytest.raises(ValidationError, match="placeholders differ"):
        load_messages(path)


def test_confirmation_templates_fill_in() -> None:
    question = MESSAGES.get("confirm_cancel", Language.VI).format(reference="VR-7K2M9Q")

    assert "VR-7K2M9Q" in question
