"""Write notifications to the outbox (FR-22). Delivery happens elsewhere.

The channel follows FR-22: Teams if the employee has it, else email, else a notice at
their next kiosk sign-in. Drivers have neither, so they see it at the kiosk.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roost.core.enums import NotificationChannel, NotificationKind
from roost.db.models import Employee, Notification, VacationRequest


def channel_for(employee: Employee) -> NotificationChannel:
    if employee.has_teams:
        return NotificationChannel.TEAMS
    if employee.email:
        return NotificationChannel.EMAIL
    return NotificationChannel.KIOSK


def request_details(request: VacationRequest) -> dict[str, Any]:
    """What a notification about a request may say: no balances, no conversation."""
    return {
        "reference": request.reference_code,
        "days": [[d.day.isoformat(), d.portion.value] for d in request.days],
    }


def notify(
    session: Session,
    recipient: Employee,
    kind: NotificationKind,
    request: VacationRequest | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Notification:
    details = (request_details(request) if request is not None else {}) | dict(extra or {})
    notification = Notification(
        recipient_id=recipient.id,
        channel=channel_for(recipient),
        kind=kind,
        request_id=request.id if request is not None else None,
        details=details,
    )
    session.add(notification)
    return notification


def take_kiosk_notices(session: Session, employee_id: Any, *, now: datetime) -> list[Notification]:
    """Unsent kiosk notices for the employee, marked sent: shown once, at sign-in."""
    notices = list(
        session.scalars(
            select(Notification)
            .where(
                Notification.recipient_id == employee_id,
                Notification.channel == NotificationChannel.KIOSK,
                Notification.sent_at.is_(None),
            )
            .order_by(Notification.id)
        )
    )
    if notices:
        session.execute(
            update(Notification)
            .where(Notification.id.in_([n.id for n in notices]))
            .values(sent_at=now)
        )
    return notices
