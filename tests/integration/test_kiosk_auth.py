"""Kiosk sign-in against real Postgres: who may set PINs, first sign-in, lockout, audit."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roost.auth.kiosk import (
    LOCKOUT,
    NotAllowedError,
    PinRejectedError,
    SignInOutcome,
    change_pin,
    set_pin,
    sign_in,
)
from roost.auth.pins import PinProblem
from roost.core.enums import AuditAction, EmploymentStatus, LocationKind, Role
from roost.db.models import AuditLog, Employee, EmployeeRole, KioskCredential, Location
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 7, 14, 0, tzinfo=UTC)
TEMP_PIN = "480715"
OWN_PIN = "902614"


def _with_role(session: Session, role: Role, location: int | None = None) -> Employee:
    query = select(Employee).join(EmployeeRole).where(EmployeeRole.role == role)
    if location is not None:
        query = query.where(Employee.location_id == location)
    return session.scalars(query.order_by(Employee.employee_number).limit(1)).one()


@pytest.fixture
def plant(db_session: Session) -> tuple[Session, Employee, Employee]:
    """A production employee at a plant, and an HR generalist at the same plant."""
    org = generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )
    load_seed_data(db_session, org, holiday_years=(2026,))
    hr = db_session.scalars(
        select(Employee)
        .join(EmployeeRole)
        .join(Location, Location.id == Employee.location_id)
        .where(EmployeeRole.role == Role.HR_GENERALIST, Location.kind == LocationKind.PLANT)
        .order_by(Employee.employee_number)
        .limit(1)
    ).one()
    worker = db_session.scalars(
        select(Employee)
        .where(Employee.location_id == hr.location_id, Employee.id != hr.id)
        .order_by(Employee.employee_number)
        .limit(1)
    ).one()
    return db_session, worker, hr


def _actions(session: Session, employee: uuid.UUID) -> list[AuditAction]:
    return list(
        session.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity_type == "employee", AuditLog.entity_id == str(employee))
            .order_by(AuditLog.id)
        )
    )


def test_first_sign_in_requires_choosing_a_new_pin(
    plant: tuple[Session, Employee, Employee],
) -> None:
    session, worker, hr = plant
    set_pin(session, worker.employee_number, TEMP_PIN, by=hr.id)

    first = sign_in(session, worker.employee_number, TEMP_PIN, now=NOW)
    assert (first.outcome, first.employee_id) == (SignInOutcome.MUST_CHANGE_PIN, worker.id)

    change_pin(session, worker.id, TEMP_PIN, OWN_PIN)

    assert (
        sign_in(session, worker.employee_number, OWN_PIN, now=NOW).outcome
        is SignInOutcome.SIGNED_IN
    )
    assert (
        sign_in(session, worker.employee_number, TEMP_PIN, now=NOW).outcome is SignInOutcome.INVALID
    )
    credential = session.get(KioskCredential, worker.id)
    assert credential is not None
    assert credential.pin_hash.startswith("$argon2id$")
    assert OWN_PIN not in credential.pin_hash
    assert _actions(session, worker.id) == [AuditAction.PIN_SET, AuditAction.PIN_CHANGED]


def test_five_wrong_pins_lock_the_account_for_fifteen_minutes(
    plant: tuple[Session, Employee, Employee],
) -> None:
    session, worker, hr = plant
    set_pin(session, worker.employee_number, TEMP_PIN, by=hr.id)

    outcomes = [
        sign_in(session, worker.employee_number, "000001", now=NOW).outcome for _ in range(5)
    ]
    assert outcomes == [SignInOutcome.INVALID] * 4 + [SignInOutcome.LOCKED]

    # Locked: even the right PIN is not checked.
    locked = sign_in(session, worker.employee_number, TEMP_PIN, now=NOW + timedelta(minutes=14))
    assert (locked.outcome, locked.locked_until) == (SignInOutcome.LOCKED, NOW + LOCKOUT)

    after = sign_in(session, worker.employee_number, TEMP_PIN, now=NOW + LOCKOUT)
    assert after.signed_in
    assert _actions(session, worker.id) == [AuditAction.PIN_SET, AuditAction.PIN_LOCKED]


def test_a_correct_pin_resets_the_failure_count(plant: tuple[Session, Employee, Employee]) -> None:
    session, worker, hr = plant
    set_pin(session, worker.employee_number, TEMP_PIN, by=hr.id)

    for _ in range(4):
        sign_in(session, worker.employee_number, "000001", now=NOW)
    assert sign_in(session, worker.employee_number, TEMP_PIN, now=NOW).signed_in
    for _ in range(4):
        assert (
            sign_in(session, worker.employee_number, "000001", now=NOW).outcome
            is SignInOutcome.INVALID
        )


def test_hr_resetting_a_pin_clears_the_lock(plant: tuple[Session, Employee, Employee]) -> None:
    session, worker, hr = plant
    set_pin(session, worker.employee_number, TEMP_PIN, by=hr.id)
    for _ in range(5):
        sign_in(session, worker.employee_number, "000001", now=NOW)

    set_pin(session, worker.employee_number, "246813", by=hr.id)

    assert (
        sign_in(session, worker.employee_number, "246813", now=NOW).outcome
        is SignInOutcome.MUST_CHANGE_PIN
    )


@pytest.mark.parametrize("number", ["999999", "", "abc"])
def test_unknown_numbers_get_the_same_answer_as_a_wrong_pin(
    plant: tuple[Session, Employee, Employee], number: str
) -> None:
    session, _, _ = plant

    assert sign_in(session, number, TEMP_PIN, now=NOW).outcome is SignInOutcome.INVALID


def test_no_pin_set_or_a_former_employee_cannot_sign_in(
    plant: tuple[Session, Employee, Employee],
) -> None:
    session, worker, hr = plant
    assert (
        sign_in(session, worker.employee_number, TEMP_PIN, now=NOW).outcome is SignInOutcome.INVALID
    )

    set_pin(session, worker.employee_number, TEMP_PIN, by=hr.id)
    session.execute(
        update(Employee)
        .where(Employee.id == worker.id)
        .values(employment_status=EmploymentStatus.TERMINATED)
    )

    assert (
        sign_in(session, worker.employee_number, TEMP_PIN, now=NOW).outcome is SignInOutcome.INVALID
    )


@pytest.mark.parametrize(
    ("new_pin", "problem"),
    [
        ("123456", PinProblem.SEQUENCE),
        (TEMP_PIN, PinProblem.SAME_AS_CURRENT),
        ("1111", PinProblem.NOT_SIX_DIGITS),
    ],
)
def test_the_new_pin_must_pass_the_policy(
    plant: tuple[Session, Employee, Employee], new_pin: str, problem: PinProblem
) -> None:
    session, worker, hr = plant
    set_pin(session, worker.employee_number, TEMP_PIN, by=hr.id)

    with pytest.raises(PinRejectedError) as raised:
        change_pin(session, worker.id, TEMP_PIN, new_pin)

    assert raised.value.problem is problem


def test_the_birth_date_is_refused_as_a_pin(plant: tuple[Session, Employee, Employee]) -> None:
    session, worker, hr = plant
    birthday = worker.birth_date.strftime("%m%d%y")

    with pytest.raises(PinRejectedError) as raised:
        set_pin(session, worker.employee_number, birthday, by=hr.id)

    assert raised.value.problem is PinProblem.BIRTH_DATE


def test_changing_a_pin_needs_the_current_one(plant: tuple[Session, Employee, Employee]) -> None:
    session, worker, hr = plant
    set_pin(session, worker.employee_number, TEMP_PIN, by=hr.id)

    with pytest.raises(NotAllowedError):
        change_pin(session, worker.id, "000001", OWN_PIN)


def test_only_hr_at_the_employees_location_or_an_hr_admin_may_set_pins(
    plant: tuple[Session, Employee, Employee],
) -> None:
    session, worker, _ = plant
    other_hr = session.scalars(
        select(Employee)
        .join(EmployeeRole)
        .where(EmployeeRole.role == Role.HR_GENERALIST, Employee.location_id != worker.location_id)
        .limit(1)
    ).one()
    admin = _with_role(session, Role.HR_ADMIN)
    coworker = session.scalars(
        select(Employee)
        .where(
            Employee.location_id == worker.location_id,
            Employee.id.not_in(select(EmployeeRole.employee_id)),
            Employee.id != worker.id,
        )
        .limit(1)
    ).one()

    for not_allowed in (other_hr, coworker, worker):
        with pytest.raises(NotAllowedError):
            set_pin(session, worker.employee_number, TEMP_PIN, by=not_allowed.id)

    set_pin(session, worker.employee_number, TEMP_PIN, by=admin.id)
    assert session.get(KioskCredential, worker.id) is not None
