"""The Phase 8 tables against real Postgres: the append-only audit log and constraints."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from roost.audit import log as audit_log
from roost.core.enums import ApprovalDecision, ApprovalStep, AuditAction
from roost.db.models import Approval, AuditLog, Employee, KioskCredential, VacationRequest
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

ARGON2 = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"


@pytest.fixture
def seeded(db_session: Session) -> Session:
    org = generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )
    load_seed_data(db_session, org, holiday_years=(2026,))
    return db_session


def _entry(session: Session) -> AuditLog:
    entry = audit_log.record(
        session,
        AuditAction.PIN_SET,
        actor=None,
        entity_type="employee",
        entity_id="100001",
        details={"by": "hr"},
    )
    session.flush()
    return entry


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'pin_changed'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
def test_the_database_rejects_rewriting_the_audit_log(db_session: Session, statement: str) -> None:
    _entry(db_session)

    # The savepoint rolls back first, so the outer transaction can still be read.
    with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
        db_session.execute(text(statement))

    assert db_session.scalar(select(AuditLog.action)) is AuditAction.PIN_SET


def test_entries_get_a_time_and_empty_details_by_default(db_session: Session) -> None:
    entry = audit_log.record(
        db_session,
        AuditAction.LEGAL_HOLD_PLACED,
        actor=uuid.uuid4(),
        entity_type="c",
        entity_id="1",
    )
    db_session.flush()
    db_session.refresh(entry)

    assert entry.details == {}
    assert entry.occurred_at <= datetime.now(UTC)


def test_content_never_goes_into_audit_details(db_session: Session) -> None:
    with pytest.raises(ValueError, match="must not hold content"):
        audit_log.record(
            db_session,
            AuditAction.HR_SUMMARY_SENT,
            actor=None,
            entity_type="conversation",
            entity_id="1",
            details={"summary": "my supervisor ..."},
        )


def _rejected(session: Session, row: object, constraint: str) -> None:
    """Adding `row` violates `constraint`; the savepoint keeps the test's transaction usable."""
    session.add(row)
    with pytest.raises(IntegrityError, match=constraint), session.begin_nested():
        session.flush()  # the rolled-back savepoint also drops `row` from the session


def _request(session: Session) -> VacationRequest:
    employee = session.scalars(select(Employee.id).limit(1)).one()
    request = VacationRequest(reference_code="VR-ABC234", employee_id=employee)
    session.add(request)
    session.flush()
    return request


def test_an_approval_step_has_exactly_one_assignee(seeded: Session) -> None:
    request = _request(seeded)
    supervisor = seeded.scalars(select(Employee.id).offset(1).limit(1)).one()
    location = seeded.scalar(select(Employee.location_id).limit(1))

    seeded.add(
        Approval(
            request_id=request.id, round=1, step=ApprovalStep.SUPERVISOR, assignee_id=supervisor
        )
    )
    seeded.add(
        Approval(request_id=request.id, round=1, step=ApprovalStep.HR, hr_location_id=location)
    )
    seeded.flush()

    _rejected(
        seeded, Approval(request_id=request.id, round=2, step=ApprovalStep.HR), "one_assignee"
    )


def test_a_decision_needs_who_and_when(seeded: Session) -> None:
    request = _request(seeded)
    location = seeded.scalar(select(Employee.location_id).limit(1))

    approved_by_nobody = Approval(
        request_id=request.id,
        round=1,
        step=ApprovalStep.HR,
        hr_location_id=location,
        decision=ApprovalDecision.APPROVED,
    )
    _rejected(seeded, approved_by_nobody, "decided_fields_match")


def test_one_step_per_round(seeded: Session) -> None:
    request = _request(seeded)
    location = seeded.scalar(select(Employee.location_id).limit(1))
    seeded.add(
        Approval(request_id=request.id, round=1, step=ApprovalStep.HR, hr_location_id=location)
    )
    seeded.flush()

    again = Approval(request_id=request.id, round=1, step=ApprovalStep.HR, hr_location_id=location)
    _rejected(seeded, again, "uq_approval_request_id")


def test_a_pin_can_only_be_stored_as_an_argon2id_hash(seeded: Session) -> None:
    employee = seeded.scalars(select(Employee.id).limit(1)).one()
    seeded.add(KioskCredential(employee_id=employee, pin_hash=ARGON2))
    seeded.flush()
    credential = seeded.get(KioskCredential, employee)
    assert credential is not None
    assert (credential.must_change, credential.failed_attempts) == (True, 0)

    other = seeded.scalars(select(Employee.id).offset(1).limit(1)).one()
    _rejected(seeded, KioskCredential(employee_id=other, pin_hash="123456"), "pin_hash_is_argon2id")
