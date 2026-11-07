"""Send an employee's own words to their HR team, with consent (FR-32, ADR-0013).

The agent offers this after a sensitive-matter reply; nothing is sent unless the
employee answers YES. Every function works in the caller's transaction. The HR notice
carries no details at all: the words are read only in the approver console, by HR at
the employee's location or an HR admin.
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.orm import Session

from roost.audit import log as audit_log
from roost.auth.console import roles_of
from roost.core.enums import AuditAction, EmploymentStatus, NotificationKind, Role
from roost.db.models import Employee, EmployeeRole, HRReferral
from roost.db.models.referral import MAX_REFERRAL_LENGTH
from roost.guardrails.types import ROUTE_OF, Route
from roost.leave.approvals import hr_team
from roost.notifications.outbox import notify
from roost.referrals.offer import ReferralDraft, ReferralError, ReferralSender

# Handled referrals keep the employee's words this long, then only the record remains.
KEEP_TEXT_AFTER_HANDLED = timedelta(days=90)


def _recipients(session: Session, employee: Employee) -> list[Employee]:
    """The HR team at the employee's location; HR admins if there is nobody there."""
    team = [hr for hr in hr_team(session, employee.location_id) if hr.id != employee.id]
    if team:
        return team
    return [
        admin
        for admin in session.scalars(
            select(Employee)
            .join(EmployeeRole)
            .where(
                EmployeeRole.role == Role.HR_ADMIN,
                Employee.employment_status == EmploymentStatus.ACTIVE,
            )
            .order_by(Employee.employee_number)
        )
        if admin.id != employee.id
    ]


def send_referral(session: Session, draft: ReferralDraft) -> HRReferral:
    """Save the employee's words for their HR team and tell the team, without details."""
    if ROUTE_OF[draft.category] is not Route.SENSITIVE:
        raise ReferralError("only sensitive matters are referred")
    text = draft.text.strip()[:MAX_REFERRAL_LENGTH]
    if not text:
        raise ReferralError("nothing to send")
    employee = session.get_one(Employee, draft.employee_id)
    referral = HRReferral(
        employee_id=employee.id,
        location_id=employee.location_id,
        category=draft.category,
        language=draft.language,
        text=text,
    )
    session.add(referral)
    session.flush()
    for hr in _recipients(session, employee):
        notify(session, hr, NotificationKind.HR_REFERRAL)
    audit_log.record(
        session,
        AuditAction.HR_SUMMARY_SENT,
        actor=employee.id,
        entity_type="hr_referral",
        entity_id=str(referral.id),
        details={"category": draft.category.value},  # never the words (FR-24)
    )
    session.flush()
    return referral


def database_referrals(
    session_factory: Callable[[], AbstractContextManager[Session]],
) -> ReferralSender:
    """What the agent calls after a YES: send and commit in one transaction."""

    def send(draft: ReferralDraft) -> None:
        with session_factory() as session:
            send_referral(session, draft)
            session.commit()

    return send


def _visible_to(session: Session, hr_id: uuid.UUID) -> list[ColumnElement[bool]] | None:
    """The filter for referrals this person may read, or None if they may read none."""
    viewer = session.get(Employee, hr_id)
    if viewer is None or viewer.employment_status is not EmploymentStatus.ACTIVE:
        return None
    roles = roles_of(session, hr_id)
    if Role.HR_ADMIN in roles:
        return [HRReferral.employee_id != hr_id]
    if Role.HR_GENERALIST in roles:
        return [HRReferral.location_id == viewer.location_id, HRReferral.employee_id != hr_id]
    return None


def may_read_referrals(session: Session, hr_id: uuid.UUID) -> bool:
    return _visible_to(session, hr_id) is not None


def referrals_for(
    session: Session, hr_id: uuid.UUID, *, handled: bool = False, limit: int = 100
) -> list[tuple[HRReferral, Employee]]:
    """Open referrals (or recently handled ones) this HR person may read, newest first."""
    filters = _visible_to(session, hr_id)
    if filters is None:
        return []
    state = HRReferral.handled_at.is_not(None) if handled else HRReferral.handled_at.is_(None)
    rows = session.execute(
        select(HRReferral, Employee)
        .join(Employee, Employee.id == HRReferral.employee_id)
        .where(state, *filters)
        .order_by(HRReferral.created_at.desc())
        .limit(limit)
    ).all()
    return [(referral, employee) for referral, employee in rows]


def referral_for(
    session: Session, hr_id: uuid.UUID, referral_id: uuid.UUID
) -> tuple[HRReferral, Employee] | None:
    """One referral, only if this HR person may read it (else None, as if it did not exist)."""
    filters = _visible_to(session, hr_id)
    if filters is None:
        return None
    row = session.execute(
        select(HRReferral, Employee)
        .join(Employee, Employee.id == HRReferral.employee_id)
        .where(HRReferral.id == referral_id, *filters)
    ).first()
    return (row[0], row[1]) if row else None


def mark_handled(
    session: Session, hr_id: uuid.UUID, referral_id: uuid.UUID, *, now: datetime
) -> bool:
    """HR has followed up. False if they may not, or it was already handled."""
    found = referral_for(session, hr_id, referral_id)
    if found is None or found[0].handled_at is not None:
        return False
    referral = found[0]
    referral.handled_at = now
    referral.handled_by_id = hr_id
    audit_log.record(
        session,
        AuditAction.HR_REFERRAL_HANDLED,
        actor=hr_id,
        entity_type="hr_referral",
        entity_id=str(referral.id),
    )
    session.flush()
    return True


def delete_old_texts(session: Session, *, now: datetime) -> int:
    """Erase the words of referrals handled more than 90 days ago; the record stays."""
    erased = session.execute(
        update(HRReferral)
        .where(
            HRReferral.text.is_not(None),
            HRReferral.handled_at < now - KEEP_TEXT_AFTER_HANDLED,
        )
        .values(text=None, text_deleted_at=now)
        .returning(HRReferral.id)
    )
    return len(erased.all())


__all__ = [
    "KEEP_TEXT_AFTER_HANDLED",
    "ReferralDraft",
    "ReferralError",
    "ReferralSender",
    "database_referrals",
    "delete_old_texts",
    "mark_handled",
    "may_read_referrals",
    "referral_for",
    "referrals_for",
    "send_referral",
]
