"""Load the facts the vacation rules need, and create, change and cancel requests.

Every function takes the employee's id and only ever touches that employee's requests:
an employee cannot read or change someone else's request by guessing a reference code.
"""

import secrets
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.orm import Session

from roost.core.enums import DayPortion, EmploymentStatus, LeaveType, VacationRequestStatus
from roost.db.models import Employee, LeaveBalance, Shift, VacationRequest, VacationRequestDay
from roost.leave.holidays import holiday_on
from roost.leave.rules import (
    EmployeeFacts,
    LeaveFacts,
    RuleCheck,
    VacationRequestDraft,
    check_request,
)

ACTIVE = (VacationRequestStatus.PENDING, VacationRequestStatus.APPROVED)
# No 0/O or 1/I: codes are read aloud at kiosks and typed back in.
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class RequestNotFoundError(LookupError):
    """No such request for this employee (deliberately the same for 'not yours')."""


class RequestNotChangeableError(ValueError):
    """The request has started, or is already cancelled or denied."""


class RuleViolationError(ValueError):
    """Raised when saving a request that does not pass the rules."""

    def __init__(self, check: RuleCheck) -> None:
        super().__init__(", ".join(v.rule.value for v in check.violations))
        self.check = check


def new_reference_code() -> str:
    """e.g. VR-7K2M9Q: random (not a counter), so codes reveal nothing about volume."""
    return "VR-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))


# --- reading facts ------------------------------------------------------------------


def load_employee_facts(session: Session, employee_id: uuid.UUID) -> EmployeeFacts:
    row = session.execute(
        select(Employee.hire_date, Employee.payroll_class, Shift.workdays, Shift.paid_hours)
        .join(Shift, Shift.id == Employee.shift_id)
        .where(Employee.id == employee_id)
    ).one()
    return EmployeeFacts(
        hire_date=row.hire_date,
        payroll_class=row.payroll_class,
        workdays=frozenset(row.workdays),
        shift_hours=row.paid_hours,
    )


def _available_hours(session: Session, employee_id: uuid.UUID, year: int) -> Decimal:
    """Accrued + carried over - used, from the latest balance year up to `year`."""
    balance = session.scalars(
        select(LeaveBalance)
        .where(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.leave_type == LeaveType.VACATION,
            LeaveBalance.year <= year,
        )
        .order_by(LeaveBalance.year.desc())
        .limit(1)
    ).first()
    if balance is None:
        return Decimal(0)
    return balance.accrued_hours + balance.carried_over_hours - balance.used_hours


def load_leave_facts(
    session: Session,
    employee_id: uuid.UUID,
    days: Sequence[date],
    *,
    ignore_request_id: uuid.UUID | None = None,
) -> LeaveFacts:
    """Facts for checking `days`. `ignore_request_id` leaves out the request being
    changed, so it does not clash with itself or count against its own hours."""
    employee = session.get_one(Employee, employee_id)
    others: ColumnElement[bool] = (
        VacationRequest.id != ignore_request_id if ignore_request_id else true()
    )

    pending_hours = session.scalar(
        select(func.coalesce(func.sum(VacationRequestDay.hours), 0))
        .join(VacationRequest)
        .where(
            VacationRequest.employee_id == employee_id,
            VacationRequest.status == VacationRequestStatus.PENDING,
            others,
        )
    )
    booked_days = session.scalars(
        select(VacationRequestDay.day)
        .join(VacationRequest)
        .where(
            VacationRequest.employee_id == employee_id,
            VacationRequest.status.in_(ACTIVE),
            others,
        )
    ).all()

    same_team = (
        Employee.department_id == employee.department_id,
        Employee.location_id == employee.location_id,
        Employee.shift_id == employee.shift_id,
        Employee.employment_status == EmploymentStatus.ACTIVE,
    )
    team_size = session.scalar(select(func.count()).select_from(Employee).where(*same_team))
    team_off = dict(
        session.execute(
            select(VacationRequestDay.day, func.count(func.distinct(VacationRequest.employee_id)))
            .join(VacationRequest)
            .join(Employee, Employee.id == VacationRequest.employee_id)
            .where(
                *same_team,
                Employee.id != employee_id,
                VacationRequest.status.in_(ACTIVE),
                VacationRequestDay.day.in_(list(days)),
            )
            .group_by(VacationRequestDay.day)
        ).all()
    )
    return LeaveFacts(
        available_hours=_available_hours(session, employee_id, max(days).year),
        pending_hours=Decimal(pending_hours or 0),
        booked_days=frozenset(booked_days),
        team_size=team_size or 0,
        team_off=team_off,
    )


def _lock_team(session: Session, employee_id: uuid.UUID) -> None:
    """Serialise check-and-save for one team, so two people cannot both take the last
    free spot (V-06) at the same moment. Released when the transaction ends."""
    employee = session.get_one(Employee, employee_id)
    key = f"vacation-team:{employee.department_id}:{employee.location_id}:{employee.shift_id}"
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))


def check_draft(
    session: Session,
    employee_id: uuid.UUID,
    draft: VacationRequestDraft,
    *,
    today: date,
    ignore_request_id: uuid.UUID | None = None,
) -> RuleCheck:
    """Run the rules against current data. Safe to call any time; changes nothing."""
    dates = [d.day for d in draft.days]
    return check_request(
        draft,
        load_employee_facts(session, employee_id),
        load_leave_facts(session, employee_id, dates, ignore_request_id=ignore_request_id),
        today=today,
    )


# --- writing requests ---------------------------------------------------------------


def _day_rows(draft: VacationRequestDraft, shift_hours: Decimal) -> list[VacationRequestDay]:
    """Day rows with the hours each one charges: 0 on observed holidays (V-09)."""
    rows = []
    for requested in draft.days:
        if holiday_on(requested.day):
            hours = Decimal(0)
        elif requested.portion is DayPortion.FULL:
            hours = shift_hours
        else:
            hours = shift_hours / 2
        rows.append(VacationRequestDay(day=requested.day, portion=requested.portion, hours=hours))
    return rows


def create_request(
    session: Session, employee_id: uuid.UUID, draft: VacationRequestDraft, *, today: date
) -> VacationRequest:
    """Re-check the rules under the team lock, then save. Raises RuleViolationError."""
    _lock_team(session, employee_id)
    check = check_draft(session, employee_id, draft, today=today)
    if not check.ok:
        raise RuleViolationError(check)
    request = VacationRequest(
        reference_code=new_reference_code(),
        employee_id=employee_id,
        days=_day_rows(draft, load_employee_facts(session, employee_id).shift_hours),
    )
    session.add(request)
    session.flush()
    return request


def get_request(session: Session, employee_id: uuid.UUID, reference_code: str) -> VacationRequest:
    request = session.scalars(
        select(VacationRequest).where(
            VacationRequest.employee_id == employee_id,
            VacationRequest.reference_code == reference_code.strip().upper(),
        )
    ).first()
    if request is None:
        raise RequestNotFoundError(reference_code)
    return request


def list_requests(session: Session, employee_id: uuid.UUID) -> list[VacationRequest]:
    """The employee's requests, newest first (FR-12)."""
    return list(
        session.scalars(
            select(VacationRequest)
            .where(VacationRequest.employee_id == employee_id)
            .order_by(VacationRequest.created_at.desc())
        )
    )


def _ensure_changeable(request: VacationRequest, today: date) -> None:
    if request.status not in ACTIVE or request.days[0].day <= today:
        raise RequestNotChangeableError(request.reference_code)


def change_request(
    session: Session,
    employee_id: uuid.UUID,
    reference_code: str,
    draft: VacationRequestDraft,
    *,
    today: date,
) -> VacationRequest:
    """Replace the days (FR-13). Any change sends the request back to pending, so both
    approvals restart."""
    request = get_request(session, employee_id, reference_code)
    _ensure_changeable(request, today)
    _lock_team(session, employee_id)
    check = check_draft(session, employee_id, draft, today=today, ignore_request_id=request.id)
    if not check.ok:
        raise RuleViolationError(check)
    request.days = _day_rows(draft, load_employee_facts(session, employee_id).shift_hours)
    request.status = VacationRequestStatus.PENDING
    session.flush()
    return request


def cancel_request(
    session: Session, employee_id: uuid.UUID, reference_code: str, *, today: date
) -> VacationRequest:
    """Cancel before the first day (FR-14)."""
    request = get_request(session, employee_id, reference_code)
    _ensure_changeable(request, today)
    request.status = VacationRequestStatus.CANCELLED
    session.flush()
    return request
