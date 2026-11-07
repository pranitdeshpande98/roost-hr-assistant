"""The vacation balance row the rules read, and posting approved hours to it.

The rules check a day against the latest balance year up to that day's year, so an
approved request's hours are posted to that same row. Checking and posting always agree.
"""

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.core.enums import LeaveType
from roost.db.models import LeaveBalance, VacationRequest


def balance_row(
    session: Session, employee_id: uuid.UUID, year: int, *, lock: bool = False
) -> LeaveBalance | None:
    """The latest vacation balance up to `year`, or None."""
    query = (
        select(LeaveBalance)
        .where(
            LeaveBalance.employee_id == employee_id,
            LeaveBalance.leave_type == LeaveType.VACATION,
            LeaveBalance.year <= year,
        )
        .order_by(LeaveBalance.year.desc())
        .limit(1)
    )
    return session.scalars(query.with_for_update() if lock else query).first()


def hours_by_row(
    session: Session, request: VacationRequest, *, lock: bool = False
) -> dict[LeaveBalance, Decimal]:
    """The request's hours, per balance row they count against."""
    by_year: dict[int, Decimal] = defaultdict(Decimal)
    for day in request.days:
        by_year[day.day.year] += day.hours
    rows: dict[LeaveBalance, Decimal] = defaultdict(Decimal)
    for year, hours in by_year.items():
        row = balance_row(session, request.employee_id, year, lock=lock)
        if row is not None:
            rows[row] += hours
    return rows


def post_hours(session: Session, request: VacationRequest) -> None:
    """An approved request uses its hours, as the HR system would record them."""
    for row, hours in hours_by_row(session, request, lock=True).items():
        row.used_hours += hours


def release_hours(session: Session, request: VacationRequest) -> None:
    """An approved request is cancelled or changed: give its hours back."""
    for row, hours in hours_by_row(session, request, lock=True).items():
        row.used_hours = max(Decimal(0), row.used_hours - hours)
