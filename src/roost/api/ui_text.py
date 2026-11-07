"""Kiosk interface text from config/ui.yaml, checked when it loads: every key has every
language, and the same {placeholders} in each."""

import string
from functools import cache
from pathlib import Path

import yaml

from roost.core.enums import Language

DEFAULT_UI_TEXT = Path(__file__).resolve().parents[3] / "config" / "ui.yaml"


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class UIText:
    def __init__(self, entries: dict[str, dict[Language, str]]) -> None:
        self._entries = entries

    def get(self, key: str, language: Language) -> str:
        return self._entries[key][language]

    def keys(self) -> set[str]:
        return set(self._entries)


def load_ui_text(path: Path = DEFAULT_UI_TEXT) -> UIText:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries: dict[str, dict[Language, str]] = {}
    for key, translations in raw.items():
        if not isinstance(key, str):  # unquoted yes/no/on/off become booleans in YAML
            raise ValueError(f"ui text key {key!r} is not a string: quote it")
        missing = {lang.value for lang in Language} - set(translations)
        if missing:
            raise ValueError(f"ui text {key!r} has no {', '.join(sorted(missing))}")
        texts = {Language(lang): str(text) for lang, text in translations.items()}
        if len({frozenset(_placeholders(t)) for t in texts.values()}) > 1:
            raise ValueError(f"ui text {key!r} uses different placeholders per language")
        entries[str(key)] = texts
    return UIText(entries)


@cache
def default_ui_text() -> UIText:
    return load_ui_text()
