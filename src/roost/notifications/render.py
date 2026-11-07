"""Turn an outbox row into words in the recipient's language (config/notifications.yaml)."""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from roost.api.ui_text import UIText, load_ui_text
from roost.core.enums import Language
from roost.db.models import Notification

DEFAULT_NOTIFICATION_TEXT = Path(__file__).resolve().parents[3] / "config" / "notifications.yaml"


@cache
def default_notification_text() -> UIText:
    return load_ui_text(DEFAULT_NOTIFICATION_TEXT)


@dataclass(frozen=True, slots=True)
class RenderedNotice:
    subject: str
    body: str
    comment: str | None  # the approver's own words, shown as written


def _dates(days: list[list[str]], text: UIText, language: Language) -> str:
    if not days:
        return ""
    first, last = days[0][0], days[-1][0]
    if first == last:
        return first
    return text.get("date_range", language).format(first=first, last=last)


def render(
    notification: Notification, language: Language, text: UIText | None = None
) -> RenderedNotice:
    text = text or default_notification_text()
    details = notification.details or {}
    fields = {
        "reference": str(details.get("reference", "")),
        "dates": _dates(list(details.get("days", [])), text, language),
    }
    kind = notification.kind.value
    comment = details.get("comment")
    return RenderedNotice(
        subject=text.get(f"subject_{kind}", language).format(**fields),
        body=text.get(f"body_{kind}", language).format(**fields),
        comment=text.get("comment", language).format(comment=comment) if comment else None,
    )
