"""Tests for roost.evals.language."""

import pytest

from roost.core.enums import Language
from roost.evals.language import detect_language


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("You get 10 vacation days each year [§6.1].", Language.EN),
        ("Usted tiene 10 días de vacaciones al año [§6.1].", Language.ES),
        ("¿Cuántos días? Recibe diez por año.", Language.ES),
        ("Mỗi năm bạn được 10 ngày nghỉ phép [§6.1].", Language.VI),
        # Section titles quoted in English do not flip a Spanish answer.
        (
            "Según la sección Vacation (PTO), usted puede pedir días libres con dos semanas.",
            Language.ES,
        ),
        # A short refusal: "a" is Spanish here too, and the accent settles it.
        ("No tengo acceso a esa información. Por favor contacte a Recursos Humanos.", Language.ES),
        ("Please contact HR about your shift swap.", Language.EN),
    ],
)
def test_detects_supported_languages(text: str, language: Language) -> None:
    assert detect_language(text) is language


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "[§6.1] 10",
        "OK",
        "No, the end.",  # "no" vs "the": a tie
        "No vacation request was submitted.",  # Phase 7 eval: "no" alone made this Spanish
    ],
)
def test_returns_none_without_evidence(text: str) -> None:
    assert detect_language(text) is None
