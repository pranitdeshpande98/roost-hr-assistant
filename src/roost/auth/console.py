"""Approver console sign-in: who may use the console, passwords, and lockout.

The console is for people with something to approve: supervisors and HR. Sign-in goes
through a `ConsoleAuthenticator`, so Phase 12 can swap local passwords for Microsoft
Entra ID without touching the pages (ADR-0013). Every function works in the caller's
transaction and writes the audit log there.
"""

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.audit import log as audit_log
from roost.auth.kiosk import (
    LOCKOUT,
    MAX_FAILED_ATTEMPTS,
    NotAllowedError,
    SignInOutcome,
    SignInResult,
)
from roost.auth.passwords import (
    PasswordProblem,
    hash_password,
    needs_rehash,
    password_problem,
    verify_password,
)
from roost.core.enums import AuditAction, EmploymentStatus, Role
from roost.db.models import ConsoleCredential, Employee, EmployeeRole

CONSOLE_ROLES = frozenset({Role.SUPERVISOR, Role.HR_GENERALIST, Role.HR_ADMIN})

# Verified when the account is unknown, so every failure takes the same time.
DUMMY_HASH = hash_password("not-a-real-account-password")


class PasswordRejectedError(ValueError):
    def __init__(self, problem: PasswordProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


class ConsoleAuthenticator(Protocol):
    """Checks who is signing in to the console. Local passwords now; Entra ID later."""

    def sign_in(
        self, session: Session, employee_number: str, secret: str, *, now: datetime
    ) -> SignInResult: ...


def roles_of(session: Session, employee_id: uuid.UUID) -> set[Role]:
    return set(
        session.scalars(select(EmployeeRole.role).where(EmployeeRole.employee_id == employee_id))
    )


def may_use_console(session: Session, employee_id: uuid.UUID) -> bool:
    """Active, and a supervisor or in HR. Checked at sign-in and on every page, so a
    role taken away ends console access at once."""
    employee = session.get(Employee, employee_id)
    if employee is None or employee.employment_status is not EmploymentStatus.ACTIVE:
        return False
    return bool(roles_of(session, employee_id) & CONSOLE_ROLES)


def _personal(employee: Employee) -> tuple[str, ...]:
    return (employee.first_name, employee.last_name, employee.employee_number)


def _locked_credential(session: Session, employee_id: uuid.UUID) -> ConsoleCredential | None:
    return session.scalars(
        select(ConsoleCredential)
        .where(ConsoleCredential.employee_id == employee_id)
        .with_for_update()
    ).one_or_none()


def _audit(
    session: Session, action: AuditAction, actor: uuid.UUID | None, employee: uuid.UUID
) -> None:
    audit_log.record(session, action, actor=actor, entity_type="employee", entity_id=str(employee))


class LocalPasswords:
    """Employee number and password, checked against console_credential."""

    def sign_in(
        self, session: Session, employee_number: str, secret: str, *, now: datetime
    ) -> SignInResult:
        employee = session.scalars(
            select(Employee).where(Employee.employee_number == employee_number.strip())
        ).one_or_none()
        allowed = employee is not None and may_use_console(session, employee.id)
        credential = _locked_credential(session, employee.id) if employee and allowed else None
        if employee is None or credential is None:
            verify_password(DUMMY_HASH, secret)  # same time as a real check
            return SignInResult(SignInOutcome.INVALID)

        if credential.locked_until is not None and credential.locked_until > now:
            return SignInResult(SignInOutcome.LOCKED, locked_until=credential.locked_until)

        if not verify_password(credential.password_hash, secret):
            credential.failed_attempts += 1
            if credential.failed_attempts >= MAX_FAILED_ATTEMPTS:
                credential.failed_attempts = 0
                credential.locked_until = now + LOCKOUT
                _audit(session, AuditAction.PASSWORD_LOCKED, None, employee.id)
                session.flush()
                return SignInResult(SignInOutcome.LOCKED, locked_until=credential.locked_until)
            session.flush()
            return SignInResult(SignInOutcome.INVALID)

        credential.failed_attempts = 0
        credential.locked_until = None
        if needs_rehash(credential.password_hash):
            credential.password_hash = hash_password(secret)
        session.flush()
        outcome = (
            SignInOutcome.MUST_CHANGE_PIN if credential.must_change else SignInOutcome.SIGNED_IN
        )
        return SignInResult(outcome, employee_id=employee.id)


def change_password(session: Session, employee_id: uuid.UUID, current: str, new: str) -> None:
    """The approver chooses a new password. Raises PasswordRejectedError, or
    NotAllowedError if the current password is wrong."""
    credential = _locked_credential(session, employee_id)
    employee = session.get(Employee, employee_id)
    if (
        credential is None
        or employee is None
        or not verify_password(credential.password_hash, current)
    ):
        raise NotAllowedError("current password does not match")
    if new == current:
        raise PasswordRejectedError(PasswordProblem.SAME_AS_CURRENT)
    problem = password_problem(new, personal=_personal(employee))
    if problem is not None:
        raise PasswordRejectedError(problem)
    credential.password_hash = hash_password(new)
    credential.must_change = False
    session.flush()
    _audit(session, AuditAction.PASSWORD_CHANGED, employee_id, employee_id)


def set_password(session: Session, employee_number: str, password: str, *, by: uuid.UUID) -> None:
    """HR sets or resets an approver's console password. The approver must change it at
    their next sign-in, and any lockout is cleared. HR admins may do it for anyone; HR
    generalists for people at their own location. Raises NotAllowedError or
    PasswordRejectedError."""
    employee = session.scalars(
        select(Employee).where(Employee.employee_number == employee_number.strip())
    ).one_or_none()
    hr = session.get(Employee, by)
    if employee is None or hr is None:
        raise NotAllowedError("unknown employee")
    hr_roles = roles_of(session, hr.id)
    may_set = Role.HR_ADMIN in hr_roles or (
        Role.HR_GENERALIST in hr_roles and hr.location_id == employee.location_id
    )
    if not may_set or hr.id == employee.id:
        raise NotAllowedError("only HR at the employee's location (or an HR admin) can set it")
    if not may_use_console(session, employee.id):
        raise NotAllowedError("the console is only for supervisors and HR")
    problem = password_problem(password, personal=_personal(employee))
    if problem is not None:
        raise PasswordRejectedError(problem)
    credential = _locked_credential(session, employee.id)
    if credential is None:
        credential = ConsoleCredential(employee_id=employee.id, password_hash="")
        session.add(credential)
    credential.password_hash = hash_password(password)
    credential.must_change = True
    credential.failed_attempts = 0
    credential.locked_until = None
    session.flush()
    _audit(session, AuditAction.PASSWORD_SET, hr.id, employee.id)
