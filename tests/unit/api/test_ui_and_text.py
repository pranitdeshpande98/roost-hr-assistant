"""Tests for kiosk interface text and plain-text replies."""

from pathlib import Path

import pytest

from roost.api.text import kiosk_text
from roost.api.ui_text import load_ui_text
from roost.core.enums import Language


def test_real_ui_text_has_every_language_and_quoted_keys() -> None:
    text = load_ui_text()

    assert {"yes", "no", "sign_in", "locked", "pin_birth_date"} <= text.keys()
    assert text.get("yes", Language.VI) == "CÓ"
    assert "{minutes}" in text.get("locked", Language.ES)


def test_an_unquoted_yes_key_is_caught(tmp_path: Path) -> None:
    path = tmp_path / "ui.yaml"
    path.write_text("yes:\n  en: Y\n  es: S\n  vi: C\n", encoding="utf-8")

    with pytest.raises(ValueError, match="quote it"):
        load_ui_text(path)


def test_placeholders_must_match_across_languages(tmp_path: Path) -> None:
    path = tmp_path / "ui.yaml"
    path.write_text(
        '"x":\n  en: "{minutes} min"\n  es: "{minutos} min"\n  vi: "{minutes}"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="different placeholders"):
        load_ui_text(path)


@pytest.mark.parametrize(
    ("markdown", "plain"),
    [
        ("You get **10 days** a year [§6.1].", "You get 10 days a year [§6.1]."),
        ("Use the `kiosk` or *Teams*.", "Use the kiosk or Teams."),
        ("## Steps\n- Call HR\n* Fill the form", "Steps\n• Call HR\n• Fill the form"),
        ("See [the policy](https://x.example) [§6.4].", "See the policy [§6.4]."),
        ("2 * 3 = 6 and 4*5", "2 * 3 = 6 and 4*5"),  # arithmetic stays
    ],
)
def test_kiosk_text_removes_markdown_but_keeps_citations(markdown: str, plain: str) -> None:
    assert kiosk_text(markdown) == plain
