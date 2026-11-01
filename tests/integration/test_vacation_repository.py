"""Vacation requests against real Postgres: facts, create, change, cancel, locking."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from roost.core.enums import DayPortion, LeaveType, VacationRequestStatus
from roost.db.models import Department, Employee, LeaveBalance, Location
from roost.leave.repository import (
    RequestNotChangeableError,
    RequestNotFoundError,
    RuleViolationError,
    cancel_request,
    change_request,
    create_request,
    get_request,
    list_requests,
    load_employee_facts,
    load_leave_facts,
)
from roost.leave.rules import RequestedDay, RuleId, VacationRequestDraft
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 2)
WEEK = [date(2027, 1, 11) + timedelta(days=i) for i in range(5)]  # Mon-Fri


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )


@pytest.fixture
def finance(db_session: Session, small_org: SeedData) -> tuple[Session, uuid.UUID, uuid.UUID]:
    """Two people on the 6-person corporate Finance team (limit: 1 off per day), both
    long-tenured with 80 vacation hours available."""
    load_seed_data(db_session, small_org, holiday_years=(2026, 2027))
    ids = db_session.scalars(
        select(Employee.id)
        .join(Department)
        .join(Location, Location.id == Employee.location_id)
        .where(Department.name == "Finance", Location.code == "TX_CORP")
        .order_by(Employee.employee_number)
        .limit(2)
    ).all()
    db_session.execute(
        update(Employee).where(Employee.id.in_(ids)).values(hire_date=date(2020, 1, 6))
    )
    db_session.execute(
        update(LeaveBalance)
        .where(LeaveBalance.employee_id.in_(ids), LeaveBalance.leave_type == LeaveType.VACATION)
        .values(accrued_hours=80, carried_over_hours=0, used_hours=0)
    )
    return db_session, ids[0], ids[1]


def _draft(*days: date) -> VacationRequestDraft:
    return VacationRequestDraft(tuple(RequestedDay(d) for d in days))


def test_employee_facts_come_from_the_shift(finance: tuple[Session, uuid.UUID, uuid.UUID]) -> None:
    session, alice, _ = finance

    facts = load_employee_facts(session, alice)

    assert facts.workdays == frozenset({1, 2, 3, 4, 5})
    assert facts.shift_hours == Decimal(8)


def test_create_saves_days_with_hours_and_a_reference(
    finance: tuple[Session, uuid.UUID, uuid.UUID],
) -> None:
    session, alice, _ = finance

    request = create_request(session, alice, _draft(*WEEK), today=TODAY)

    assert request.status is VacationRequestStatus.PENDING
    assert request.reference_code.startswith("VR-")
    assert [d.day for d in request.days] == WEEK
    assert sum(d.hours for d in request.days) == Decimal(40)
    assert list_requests(session, alice) == [request]


def test_pending_requests_feed_the_next_check(
    finance: tuple[Session, uuid.UUID, uuid.UUID],
) -> None:
    session, alice, _ = finance
    create_request(session, alice, _draft(*WEEK), today=TODAY)

    facts = load_leave_facts(session, alice, [date(2027, 2, 1)])

    assert facts.pending_hours == Decimal(40)
    assert facts.booked_days == frozenset(WEEK)
    assert facts.available_hours == Decimal(80)


def test_overlapping_request_is_refused(finance: tuple[Session, uuid.UUID, uuid.UUID]) -> None:
    session, alice, _ = finance
    create_request(session, alice, _draft(*WEEK), today=TODAY)

    with pytest.raises(RuleViolationError) as caught:
        create_request(session, alice, _draft(WEEK[2]), today=TODAY)

    assert {v.rule for v in caught.value.check.violations} == {RuleId.FUTURE_AND_NO_OVERLAP}


def test_team_limit_counts_teammates(finance: tuple[Session, uuid.UUID, uuid.UUID]) -> None:
    session, alice, bob = finance
    create_request(session, alice, _draft(WEEK[0]), today=TODAY)

    with pytest.raises(RuleViolationError) as caught:
        create_request(session, bob, _draft(WEEK[0], WEEK[1]), today=TODAY)

    violation = caught.value.check.violations[0]
    assert violation.rule is RuleId.TEAM_LIMIT
    assert violation.dates == (WEEK[0],)  # Tuesday was still free


def test_holiday_is_stored_with_zero_hours(finance: tuple[Session, uuid.UUID, uuid.UUID]) -> None:
    session, alice, _ = finance

    request = create_request(
        session, alice, _draft(date(2027, 5, 31), date(2027, 6, 1)), today=TODAY
    )

    assert [d.hours for d in request.days] == [Decimal(0), Decimal(8)]  # Memorial Day


def test_half_day_is_stored_as_half_a_shift(finance: tuple[Session, uuid.UUID, uuid.UUID]) -> None:
    session, alice, _ = finance
    draft = VacationRequestDraft((RequestedDay(WEEK[0], DayPortion.HALF),))

    request = create_request(session, alice, draft, today=TODAY)

    assert request.days[0].hours == Decimal(4)


def test_change_replaces_days_and_restarts_approval(
    finance: tuple[Session, uuid.UUID, uuid.UUID],
) -> None:
    session, alice, _ = finance
    request = create_request(session, alice, _draft(*WEEK), today=TODAY)
    request.status = VacationRequestStatus.APPROVED
    session.flush()
    version = request.version

    # Overlaps its own old days: allowed, because the request is checked without itself.
    changed = change_request(session, alice, request.reference_code, _draft(*WEEK[1:]), today=TODAY)

    assert [d.day for d in changed.days] == WEEK[1:]
    assert changed.status is VacationRequestStatus.PENDING  # FR-13
    assert changed.version > version


def test_cancel_frees_the_team_spot(finance: tuple[Session, uuid.UUID, uuid.UUID]) -> None:
    session, alice, bob = finance
    request = create_request(session, alice, _draft(WEEK[0]), today=TODAY)

    cancel_request(session, alice, request.reference_code, today=TODAY)

    assert request.status is VacationRequestStatus.CANCELLED
    assert create_request(session, bob, _draft(WEEK[0]), today=TODAY)


def test_cannot_cancel_once_the_vacation_has_started(
    finance: tuple[Session, uuid.UUID, uuid.UUID],
) -> None:
    session, alice, _ = finance
    request = create_request(session, alice, _draft(*WEEK), today=TODAY)

    with pytest.raises(RequestNotChangeableError):
        cancel_request(session, alice, request.reference_code, today=WEEK[0])


def test_another_employees_request_is_invisible(
    finance: tuple[Session, uuid.UUID, uuid.UUID],
) -> None:
    session, alice, bob = finance
    request = create_request(session, alice, _draft(*WEEK), today=TODAY)

    with pytest.raises(RequestNotFoundError):
        get_request(session, bob, request.reference_code)
    with pytest.raises(RequestNotFoundError):
        cancel_request(session, bob, request.reference_code, today=TODAY)


def test_reference_lookup_ignores_case_and_spaces(
    finance: tuple[Session, uuid.UUID, uuid.UUID],
) -> None:
    session, alice, _ = finance
    request = create_request(session, alice, _draft(*WEEK), today=TODAY)

    assert get_request(session, alice, f"  {request.reference_code.lower()} ") is request


def test_stale_update_is_rejected(finance: tuple[Session, uuid.UUID, uuid.UUID]) -> None:
    session, alice, _ = finance
    request = create_request(session, alice, _draft(*WEEK), today=TODAY)
    # Someone else (e.g. an approver) changes the row after we loaded it.
    session.execute(
        text("UPDATE vacation_request SET version = version + 1 WHERE id = :id"),
        {"id": request.id},
    )

    request.status = VacationRequestStatus.CANCELLED
    with pytest.raises(StaleDataError):
        session.flush()
