"""Kiosk sign-in (FR-40, FR-41): employee number + PIN, lockout, and PIN changes.

Every function works in the caller's transaction and writes the audit log there, so a
PIN change and its audit entry are saved together.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.audit import log as audit_log
from roost.auth.pins import DUMMY_HASH, PinProblem, hash_pin, needs_rehash, pin_problem, verify_pin
from roost.core.enums import AuditAction, EmploymentStatus, Role
from roost.db.models import Employee, EmployeeRole, KioskCredential

MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)


class SignInOutcome(StrEnum):
    SIGNED_IN = "signed_in"
    MUST_CHANGE_PIN = "must_change_pin"  # signed in, but only to choose a new PIN
    INVALID = "invalid"  # unknown number, no PIN set, inactive, or wrong PIN: one answer
    LOCKED = "locked"


@dataclass(frozen=True, slots=True)
class SignInResult:
    outcome: SignInOutcome
    employee_id: uuid.UUID | None = None
    locked_until: datetime | None = None

    @property
    def signed_in(self) -> bool:
        return self.outcome in (SignInOutcome.SIGNED_IN, SignInOutcome.MUST_CHANGE_PIN)


class PinRejectedError(ValueError):
    def __init__(self, problem: PinProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


class NotAllowedError(PermissionError):
    """The person asking may not set this employee's PIN."""


def _active_employee(session: Session, employee_number: str) -> Employee | None:
    return session.scalars(
        select(Employee).where(
            Employee.employee_number == employee_number.strip(),
            Employee.employment_status == EmploymentStatus.ACTIVE,
        )
    ).one_or_none()


def _locked_credential(session: Session, employee_id: uuid.UUID) -> KioskCredential | None:
    """The credential row, locked until the transaction ends: two wrong guesses at the same
    moment must both count."""
    return session.scalars(
        select(KioskCredential).where(KioskCredential.employee_id == employee_id).with_for_update()
    ).one_or_none()


def _audit(
    session: Session, action: AuditAction, actor: uuid.UUID | None, employee: uuid.UUID
) -> None:
    audit_log.record(session, action, actor=actor, entity_type="employee", entity_id=str(employee))


def sign_in(session: Session, employee_number: str, pin: str, *, now: datetime) -> SignInResult:
    """Check a PIN. Five failures in a row lock the account for 15 minutes (FR-40)."""
    employee = _active_employee(session, employee_number)
    credential = _locked_credential(session, employee.id) if employee else None
    if employee is None or credential is None:
        verify_pin(DUMMY_HASH, pin)  # same time as a real check: numbers stay private
        return SignInResult(SignInOutcome.INVALID)

    if credential.locked_until is not None and credential.locked_until > now:
        return SignInResult(SignInOutcome.LOCKED, locked_until=credential.locked_until)

    if not verify_pin(credential.pin_hash, pin):
        credential.failed_attempts += 1
        if credential.failed_attempts >= MAX_FAILED_ATTEMPTS:
            credential.failed_attempts = 0
            credential.locked_until = now + LOCKOUT
            _audit(session, AuditAction.PIN_LOCKED, None, employee.id)
            session.flush()
            return SignInResult(SignInOutcome.LOCKED, locked_until=credential.locked_until)
        session.flush()
        return SignInResult(SignInOutcome.INVALID)

    credential.failed_attempts = 0
    credential.locked_until = None
    if needs_rehash(credential.pin_hash):
        credential.pin_hash = hash_pin(pin)
    session.flush()
    outcome = SignInOutcome.MUST_CHANGE_PIN if credential.must_change else SignInOutcome.SIGNED_IN
    return SignInResult(outcome, employee_id=employee.id)


def change_pin(session: Session, employee_id: uuid.UUID, current_pin: str, new_pin: str) -> None:
    """The employee chooses a new PIN, at first sign-in or any time after (FR-41).
    Raises PinRejectedError, or NotAllowedError if the current PIN is wrong."""
    credential = _locked_credential(session, employee_id)
    employee = session.get(Employee, employee_id)
    if credential is None or employee is None or not verify_pin(credential.pin_hash, current_pin):
        raise NotAllowedError("current PIN does not match")
    if new_pin == current_pin:
        raise PinRejectedError(PinProblem.SAME_AS_CURRENT)
    problem = pin_problem(new_pin, employee.birth_date)
    if problem is not None:
        raise PinRejectedError(problem)
    credential.pin_hash = hash_pin(new_pin)
    credential.must_change = False
    session.flush()
    _audit(session, AuditAction.PIN_CHANGED, employee_id, employee_id)


def _may_set_pins_for(session: Session, hr: Employee, employee: Employee) -> bool:
    """HR admins anywhere; HR generalists only for employees at their own location."""
    roles = set(session.scalars(select(EmployeeRole.role).where(EmployeeRole.employee_id == hr.id)))
    if Role.HR_ADMIN in roles:
        return True
    return Role.HR_GENERALIST in roles and hr.location_id == employee.location_id


def set_pin(session: Session, employee_number: str, pin: str, *, by: uuid.UUID) -> None:
    """HR sets or resets a PIN in person (FR-41). The employee must change it at their
    next sign-in, and any lockout is cleared. Raises PinRejectedError or NotAllowedError."""
    employee = _active_employee(session, employee_number)
    hr = session.get(Employee, by)
    if employee is None or hr is None or not _may_set_pins_for(session, hr, employee):
        raise NotAllowedError("only HR at the employee's location (or an HR admin) can set PINs")
    problem = pin_problem(pin, employee.birth_date)
    if problem is not None:
        raise PinRejectedError(problem)
    pin_hash = hash_pin(pin)
    credential = _locked_credential(session, employee.id)
    if credential is None:
        credential = KioskCredential(employee_id=employee.id, pin_hash=pin_hash)
        session.add(credential)
    credential.pin_hash = pin_hash
    credential.must_change = True
    credential.failed_attempts = 0
    credential.locked_until = None
    session.flush()
    _audit(session, AuditAction.PIN_SET, hr.id, employee.id)
