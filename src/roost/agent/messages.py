"""Fixed reply text loaded from config/messages.yaml (ADR-0006, decision 4)."""

import string
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, RootModel, field_validator

from roost.core.enums import Language

MessageKey = Literal[
    "disclaimer",
    "binding_notice",
    "no_answer",
    "confirm_submit",
    "confirm_change",
    "confirm_cancel",
    "full_day",
    "half_day",
]

# src/roost/agent/messages.py -> repository root -> config/messages.yaml
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "messages.yaml"


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class Translations(RootModel[dict[Language, str]]):
    """One message in every supported language."""

    @field_validator("root")
    @classmethod
    def _all_languages(cls, value: dict[Language, str]) -> dict[Language, str]:
        missing = set(Language) - set(value)
        if missing:
            raise ValueError(f"missing languages: {', '.join(sorted(missing))}")
        # A translation that drops or renames a {placeholder} would crash at format time.
        fields = {lang: _placeholders(text) for lang, text in value.items()}
        if len(set(map(frozenset, fields.values()))) > 1:
            raise ValueError(f"placeholders differ between languages: {fields}")
        return value


class MessageCatalog(BaseModel):
    """Every fixed message, validated when loaded so a gap fails at startup, not mid-chat."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disclaimer: Translations
    binding_notice: Translations
    no_answer: Translations
    confirm_submit: Translations
    confirm_change: Translations
    confirm_cancel: Translations
    full_day: Translations
    half_day: Translations

    def get(self, key: MessageKey, language: Language) -> str:
        """The message in one language (may be empty, meaning 'add nothing')."""
        translations: Translations = getattr(self, key)
        return translations.root[language]


def load_messages(path: Path = DEFAULT_PATH) -> MessageCatalog:
    """Read and validate the message file."""
    return MessageCatalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@lru_cache
def default_messages() -> MessageCatalog:
    """The repository's message file, loaded once per process."""
    return load_messages()


def compose_reply(answer: str | None, language: Language, messages: MessageCatalog) -> str:
    """What the employee sees: the answer (or a fixed fallback), the binding-translation
    notice for non-English replies, and the disclaimer footer on every reply (FR-03, FR-04)."""
    parts = [
        (answer or "").strip() or messages.get("no_answer", language),
        messages.get("binding_notice", language),
        messages.get("disclaimer", language),
    ]
    return "\n\n".join(part for part in parts if part)
