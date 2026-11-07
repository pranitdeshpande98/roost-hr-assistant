"""Deliver email and Teams notifications (FR-22). Until Phase 12 connects Teams and a
mail service, each message is written as a JSON file into a local folder."""

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.core.enums import NotificationChannel
from roost.db.models import Employee, Notification
from roost.notifications.render import render


def deliver_to_folder(session: Session, folder: Path, *, now: datetime) -> int:
    """Write every unsent email or Teams row to `folder` and mark it sent."""
    folder.mkdir(parents=True, exist_ok=True)
    rows = session.execute(
        select(Notification, Employee)
        .join(Employee, Employee.id == Notification.recipient_id)
        .where(
            Notification.sent_at.is_(None),
            Notification.channel.in_([NotificationChannel.EMAIL, NotificationChannel.TEAMS]),
        )
        .order_by(Notification.id)
        .with_for_update(of=Notification, skip_locked=True)  # two runs never send twice
    ).all()
    for notification, recipient in rows:
        notice = render(notification, recipient.preferred_language)
        message = {
            "channel": notification.channel.value,
            "to": recipient.email or recipient.employee_number,
            "subject": notice.subject,
            "body": "\n\n".join(filter(None, [notice.body, notice.comment])),
        }
        path = folder / f"{notification.id:08d}-{notification.channel.value}.json"
        path.write_text(json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8")
        notification.sent_at = now
    return len(rows)
