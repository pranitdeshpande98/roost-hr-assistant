"""Lightweight language identification for EN / ES / VI answers.

Deterministic and dependency-free: Vietnamese is recognised by letters that only it
uses; Spanish and English by their most common function words. Good enough to check
"did the model answer in the user's language", which is all the eval needs.
"""

import re

from roost.core.enums import Language

# Letters that appear in Vietnamese and never in English or Spanish.
_VIETNAMESE_LETTERS = frozenset("ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")
# Vietnamese is ruled out first, so accented vowels here point to Spanish.
_SPANISH_MARKS = frozenset("ñ¿¡áéíóú")


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_SPANISH_WORDS = _words(
    "el la los las de del que y en un una por para con su sus es se no al lo como más "
    "usted puede debe días tiene si pero también"
)
# Not "a": it is also a Spanish word ("contacte a Recursos Humanos").
_ENGLISH_WORDS = _words(
    "the and you your of to is for in are with an can must days have if but also this that be will"
)
_WORD = re.compile(r"[a-zñáéíóúü]+")


def detect_language(text: str) -> Language | None:
    """Best guess at the language of `text`, or None if there is nothing to go on."""
    lower = text.lower()
    letters = [ch for ch in lower if ch.isalpha()]
    if not letters:
        return None
    vietnamese_share = sum(ch in _VIETNAMESE_LETTERS for ch in letters) / len(letters)
    if vietnamese_share >= 0.02:
        return Language.VI

    words = _WORD.findall(lower)
    spanish = sum(w in _SPANISH_WORDS for w in words) + 3 * sum(
        ch in _SPANISH_MARKS for ch in lower
    )
    english = sum(w in _ENGLISH_WORDS for w in words)
    if spanish == english:
        return None  # nothing to go on, or a tie: not counted either way
    return Language.ES if spanish > english else Language.EN
