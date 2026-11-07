"""When the agent offers to send HR the employee's words (FR-32). No database here: the
agent graph imports this, and the sending is wired in by the factory (service.py)."""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from roost.core.enums import Language
from roost.guardrails.types import ROUTE_OF, Category, Route

# Someone who wants to stay anonymous should use the hotline: Roost does not offer to
# send their words to HR. Matched case-insensitively, without accents mattering much.
_ANONYMITY = re.compile(
    r"anonym|anónim|anonim|hotline|línea de ética|linea de etica|ẩn danh|an danh"
    r"|đường dây nóng|duong day nong|without my name|sin (mi|dar mi) nombre|không (nêu|cho) tên",
    re.IGNORECASE,
)


class ReferralError(ValueError):
    """The referral cannot be made (not a sensitive matter, or nothing to send)."""


@dataclass(frozen=True, slots=True)
class ReferralDraft:
    employee_id: uuid.UUID
    category: Category
    language: Language
    text: str


ReferralSender = Callable[[ReferralDraft], None]


def wants_anonymity(text: str) -> bool:
    """Whether the employee asks to stay anonymous or mentions the hotline."""
    return _ANONYMITY.search(text) is not None


def may_offer(category: Category | None, employee_id: uuid.UUID | None, text: str) -> bool:
    """FR-32: offer only for a sensitive matter (never an emergency), only to a signed-in
    employee, and never when they want to stay anonymous."""
    return (
        category is not None
        and ROUTE_OF[category] is Route.SENSITIVE
        and employee_id is not None
        and bool(text.strip())
        and not wants_anonymity(text)
    )
