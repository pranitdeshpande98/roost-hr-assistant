"""Read an employee's reply to "submit this?" deterministically: the LLM never decides."""

import re
from dataclasses import dataclass
from enum import StrEnum

YES_WORDS = frozenset({"yes", "y", "ok", "okay", "confirm", "sí", "si", "có", "vâng", "đồng ý"})
NO_WORDS = frozenset({"no", "n", "cancel", "không", "khong", "hủy"})


class Confirmation(StrEnum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


def parse_confirmation(reply: str) -> Confirmation:
    """YES or NO only for a clear one-word (or 'đồng ý') answer; anything else is UNCLEAR,
    so "yes but change Friday" is never taken as consent to submit."""
    text = re.sub(r"[.!¡¿?,\s]+", " ", reply.strip().lower()).strip()
    if text in YES_WORDS:
        return Confirmation.YES
    if text in NO_WORDS:
        return Confirmation.NO
    return Confirmation.UNCLEAR


@dataclass(frozen=True, slots=True)
class ConfirmationReply:
    """The employee's answer, and their exact words (so an unclear reply is not lost)."""

    answer: Confirmation
    text: str

    @classmethod
    def from_text(cls, text: str) -> "ConfirmationReply":
        return cls(parse_confirmation(text), text)
