"""Approval routing and decisions against real Postgres (FR-20 to FR-23, section 4)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roost.core.enums import (
    ApprovalDecision,
    ApprovalStep,
    AuditAction,
    LeaveType,
    LocationKind,
    NotificationChannel,
    NotificationKind,
    Role,
    VacationRequestStatus,
)
from roost.db.models import (
    Approval,
    AuditLog,
    Department,
    Employee,
    EmployeeRole,
    LeaveBalance,
    Location,
    Notification,
    VacationRequest,
)
from roost.leave.approvals import (
    ApprovalError,
    NotYourApprovalError,
    decide,
    hr_team,
    pending_for,
    send_overdue_notices,
)
from roost.leave.repository import (
    cancel_request,
    change_request,
    create_request,
    load_employee_facts,
)
from roost.leave.rules import RequestedDay, VacationRequestDraft
from roost.notifications.outbox import take_kiosk_notices
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
WEEK = [date(2027, 1, 11) + timedelta(days=i) for i in range(5)]  # Mon-Fri
NEXT_WEEK = [d + timedelta(days=7) for d in WEEK]


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )


class Org:
    def __init__(self, session: Session) -> None:
        self.session = session
        alice = session.scalars(
            select(Employee)
            .join(Department)
            .join(Location, Location.id == Employee.location_id)
            .where(Department.name == "Finance", Location.code == "TX_CORP")
            .order_by(Employee.employee_number)
        ).first()
        assert alice is not None
        assert alice.supervisor_id is not None
        self.alice: Employee = alice
        self.supervisor = session.get_one(Employee, alice.supervisor_id)
        team = [e for e in hr_team(session, alice.location_id) if e.id != alice.id]
        self.hr = team[0]
        other_hr = session.scalars(
            select(Employee)
            .join(EmployeeRole)
            .where(
                EmployeeRole.role == Role.HR_GENERALIST,
                Employee.location_id != alice.location_id,
            )
        ).first()
        admin = session.scalars(
            select(Employee).join(EmployeeRole).where(EmployeeRole.role == Role.HR_ADMIN)
        ).first()
        assert other_hr is not None
        assert admin is not None
        self.other_hr: Employee = other_hr
        self.admin: Employee = admin
        self.ceo = session.scalars(select(Employee).where(Employee.supervisor_id.is_(None))).one()

    def used(self, employee: Employee) -> Decimal:
        return self.session.scalars(
            select(LeaveBalance.used_hours)
            .where(
                LeaveBalance.employee_id == employee.id,
                LeaveBalance.leave_type == LeaveType.VACATION,
                LeaveBalance.year <= 2027,
            )
            .order_by(LeaveBalance.year.desc())
            .limit(1)
        ).one()

    def steps(self, request: VacationRequest, round_: int) -> dict[ApprovalStep, Approval]:
        rows = self.session.scalars(
            select(Approval).where(Approval.request_id == request.id, Approval.round == round_)
        )
        return {a.step: a for a in rows}

    def notices(self, kind: NotificationKind) -> list[Notification]:
        return list(
            self.session.scalars(
                select(Notification).where(Notification.kind == kind).order_by(Notification.id)
            )
        )


@pytest.fixture
def org(db_session: Session, small_org: SeedData) -> Org:
    load_seed_data(db_session, small_org, holiday_years=(2026, 2027))
    db_session.execute(
        update(Employee)
        .where(Employee.supervisor_id.is_not(None))
        .values(hire_date=date(2020, 1, 6))
    )
    db_session.execute(
        update(LeaveBalance)
        .where(LeaveBalance.leave_type == LeaveType.VACATION)
        .values(accrued_hours=80, carried_over_hours=0, used_hours=0)
    )
    return Org(db_session)


def _draft(*days: date) -> VacationRequestDraft:
    return VacationRequestDraft(tuple(RequestedDay(d) for d in days))


def _submit(org: Org, employee: Employee | None = None, days: list[date] = WEEK) -> VacationRequest:
    return create_request(org.session, (employee or org.alice).id, _draft(*days), today=TODAY)


def _approve_both(org: Org, request: VacationRequest, round_: int = 1) -> None:
    steps = org.steps(request, round_)
    decide(
        org.session,
        steps[ApprovalStep.SUPERVISOR].id,
        org.supervisor.id,
        ApprovalDecision.APPROVED,
        now=NOW,
    )
    decide(org.session, steps[ApprovalStep.HR].id, org.hr.id, ApprovalDecision.APPROVED, now=NOW)


def test_submit_starts_a_round_and_tells_the_supervisor(org: Org) -> None:
    request = _submit(org)

    steps = org.steps(request, 1)
    assert steps[ApprovalStep.SUPERVISOR].assignee_id == org.supervisor.id
    assert steps[ApprovalStep.HR].hr_location_id == org.alice.location_id
    assert [n.recipient_id for n in org.notices(NotificationKind.APPROVAL_NEEDED)] == [
        org.supervisor.id
    ]
    notice = org.notices(NotificationKind.APPROVAL_NEEDED)[0]
    assert notice.details == {
        "reference": request.reference_code,
        "days": [[d.isoformat(), "full"] for d in WEEK],
    }


def test_hr_sees_the_request_only_after_the_supervisor_approves(org: Org) -> None:
    request = _submit(org)
    steps = org.steps(request, 1)

    assert [p.request.id for p in pending_for(org.session, org.supervisor.id)] == [request.id]
    assert pending_for(org.session, org.hr.id) == []
    with pytest.raises(ApprovalError, match="supervisor decides first"):
        decide(
            org.session, steps[ApprovalStep.HR].id, org.hr.id, ApprovalDecision.APPROVED, now=NOW
        )

    decide(
        org.session,
        steps[ApprovalStep.SUPERVISOR].id,
        org.supervisor.id,
        ApprovalDecision.APPROVED,
        now=NOW,
    )

    assert pending_for(org.session, org.supervisor.id) == []
    assert [p.approval.id for p in pending_for(org.session, org.hr.id)] == [
        steps[ApprovalStep.HR].id
    ]
    told = {n.recipient_id for n in org.notices(NotificationKind.APPROVAL_NEEDED)}
    assert org.hr.id in told
    assert request.status is VacationRequestStatus.PENDING


def test_only_the_routed_people_may_decide(org: Org) -> None:
    request = _submit(org)
    steps = org.steps(request, 1)

    for outsider in (org.hr, org.other_hr, org.alice):
        with pytest.raises(NotYourApprovalError):
            decide(
                org.session,
                steps[ApprovalStep.SUPERVISOR].id,
                outsider.id,
                ApprovalDecision.APPROVED,
                now=NOW,
            )
    decide(
        org.session,
        steps[ApprovalStep.SUPERVISOR].id,
        org.supervisor.id,
        ApprovalDecision.APPROVED,
        now=NOW,
    )

    assert pending_for(org.session, org.other_hr.id) == []
    with pytest.raises(NotYourApprovalError):
        decide(
            org.session,
            steps[ApprovalStep.HR].id,
            org.other_hr.id,
            ApprovalDecision.APPROVED,
            now=NOW,
        )
    # An HR admin may decide for any location.
    assert [p.request.id for p in pending_for(org.session, org.admin.id)] == [request.id]
    decide(org.session, steps[ApprovalStep.HR].id, org.admin.id, ApprovalDecision.APPROVED, now=NOW)
    assert request.status is VacationRequestStatus.APPROVED


def test_nobody_approves_their_own_request(org: Org) -> None:
    request = _submit(org, org.hr)
    steps = org.steps(request, 1)
    supervisor = org.session.get_one(Employee, org.hr.supervisor_id)
    decide(
        org.session,
        steps[ApprovalStep.SUPERVISOR].id,
        supervisor.id,
        ApprovalDecision.APPROVED,
        now=NOW,
    )

    assert request.id not in {p.request.id for p in pending_for(org.session, org.hr.id)}
    with pytest.raises(NotYourApprovalError, match="own request"):
        decide(
            org.session, steps[ApprovalStep.HR].id, org.hr.id, ApprovalDecision.APPROVED, now=NOW
        )


def test_hr_approval_approves_posts_the_hours_and_tells_the_employee(org: Org) -> None:
    request = _submit(org)

    _approve_both(org, request)

    assert request.status is VacationRequestStatus.APPROVED
    assert org.used(org.alice) == Decimal(40)
    assert [n.recipient_id for n in org.notices(NotificationKind.REQUEST_APPROVED)] == [
        org.alice.id
    ]
    actions = org.session.scalars(
        select(AuditLog.action).where(AuditLog.entity_id == str(request.id)).order_by(AuditLog.id)
    ).all()
    assert actions == [
        AuditAction.REQUEST_SUBMITTED,
        AuditAction.APPROVAL_DECIDED,
        AuditAction.APPROVAL_DECIDED,
        AuditAction.REQUEST_APPROVED,
    ]
    with pytest.raises(ApprovalError, match="already decided"):
        decide(
            org.session,
            org.steps(request, 1)[ApprovalStep.HR].id,
            org.hr.id,
            ApprovalDecision.DENIED,
            now=NOW,
        )


def test_a_denial_ends_the_request_and_carries_the_comment(org: Org) -> None:
    request = _submit(org)
    supervisor_step = org.steps(request, 1)[ApprovalStep.SUPERVISOR]

    decide(
        org.session,
        supervisor_step.id,
        org.supervisor.id,
        ApprovalDecision.DENIED,
        comment="  Quarter close that week.  ",
        now=NOW,
    )

    assert request.status is VacationRequestStatus.DENIED
    assert supervisor_step.comment == "Quarter close that week."
    denied = org.notices(NotificationKind.REQUEST_DENIED)
    assert [n.recipient_id for n in denied] == [org.alice.id]
    assert denied[0].details["comment"] == "Quarter close that week."
    assert org.used(org.alice) == Decimal(0)
    assert pending_for(org.session, org.hr.id) == []


def test_a_change_restarts_approval_and_gives_approved_hours_back(org: Org) -> None:
    request = _submit(org)
    _approve_both(org, request)

    # 40 approved hours are used; the new 80 hours fit because the old 40 come back.
    change_request(
        org.session, org.alice.id, request.reference_code, _draft(*WEEK, *NEXT_WEEK), today=TODAY
    )

    assert request.status is VacationRequestStatus.PENDING
    assert org.used(org.alice) == Decimal(0)
    assert set(org.steps(request, 2)) == {ApprovalStep.SUPERVISOR, ApprovalStep.HR}
    _approve_both(org, request, round_=2)
    assert org.used(org.alice) == Decimal(80)


def test_a_decision_on_an_old_round_is_refused(org: Org) -> None:
    request = _submit(org)
    old = org.steps(request, 1)
    decide(
        org.session,
        old[ApprovalStep.SUPERVISOR].id,
        org.supervisor.id,
        ApprovalDecision.APPROVED,
        now=NOW,
    )

    change_request(
        org.session, org.alice.id, request.reference_code, _draft(*NEXT_WEEK), today=TODAY
    )

    with pytest.raises(ApprovalError, match="changed since"):
        decide(org.session, old[ApprovalStep.HR].id, org.hr.id, ApprovalDecision.APPROVED, now=NOW)
    assert [p.approval.round for p in pending_for(org.session, org.supervisor.id)] == [2]


def test_cancelling_an_approved_request_gives_hours_back_and_tells_the_approvers(org: Org) -> None:
    request = _submit(org)
    _approve_both(org, request)

    cancel_request(org.session, org.alice.id, request.reference_code, today=TODAY)

    assert request.status is VacationRequestStatus.CANCELLED
    assert org.used(org.alice) == Decimal(0)
    told = {n.recipient_id for n in org.notices(NotificationKind.REQUEST_CANCELLED)}
    assert org.supervisor.id in told
    assert org.hr.id in told
    assert org.alice.id not in told


def test_cancelling_before_the_supervisor_decides_tells_only_the_supervisor(org: Org) -> None:
    request = _submit(org)

    cancel_request(org.session, org.alice.id, request.reference_code, today=TODAY)

    told = [n.recipient_id for n in org.notices(NotificationKind.REQUEST_CANCELLED)]
    assert told == [org.supervisor.id]
    assert pending_for(org.session, org.supervisor.id) == []


def test_the_ceo_needs_no_approval_and_corporate_hr_is_told(org: Org) -> None:
    request = _submit(org, org.ceo)

    assert request.status is VacationRequestStatus.APPROVED
    assert org.steps(request, 1) == {}
    assert org.used(org.ceo) == Decimal(40)
    corporate = org.session.scalars(
        select(Location.id).where(Location.kind == LocationKind.CORPORATE)
    ).one()
    told = {n.recipient_id for n in org.notices(NotificationKind.CEO_REQUEST)}
    assert told == {e.id for e in hr_team(org.session, corporate)}
    assert told


def test_an_overdue_decision_tells_the_employee_once_per_round(org: Org) -> None:
    request = _submit(org)
    started = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)  # a Tuesday
    org.session.execute(
        update(Approval).where(Approval.request_id == request.id).values(created_at=started)
    )

    assert send_overdue_notices(org.session, today=date(2026, 10, 29)) == []  # 2 business days
    assert send_overdue_notices(org.session, today=date(2026, 10, 30)) == [request]  # 3
    assert send_overdue_notices(org.session, today=date(2026, 11, 2)) == []  # once per round

    change_request(
        org.session, org.alice.id, request.reference_code, _draft(*NEXT_WEEK), today=TODAY
    )
    org.session.execute(
        update(Approval)
        .where(Approval.request_id == request.id, Approval.round == 2)
        .values(created_at=started)
    )
    assert send_overdue_notices(org.session, today=date(2026, 10, 30)) == [request]
    notices = org.notices(NotificationKind.DECISION_OVERDUE)
    assert [(n.recipient_id, n.details["round"]) for n in notices] == [
        (org.alice.id, 1),
        (org.alice.id, 2),
    ]


def test_a_start_two_days_away_is_overdue_at_once(org: Org) -> None:
    first_day = date(2026, 11, 16)  # a Monday, 14 days' notice
    request = create_request(org.session, org.alice.id, _draft(first_day), today=TODAY)
    started = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    org.session.execute(
        update(Approval).where(Approval.request_id == request.id).values(created_at=started)
    )

    assert send_overdue_notices(org.session, today=date(2026, 11, 13)) == []
    assert send_overdue_notices(org.session, today=date(2026, 11, 14)) == [request]


def test_kiosk_notices_are_shown_once(org: Org) -> None:
    driver = org.session.scalars(
        select(Employee).where(
            Employee.email.is_(None),
            Employee.has_teams.is_(False),
            Employee.supervisor_id.is_not(None),
        )
    ).first()
    assert driver is not None
    workdays = load_employee_facts(org.session, driver.id).workdays
    day = next(d for d in WEEK if d.isoweekday() in workdays)
    request = _submit(org, driver, [day])
    steps = org.steps(request, 1)
    decide(
        org.session,
        steps[ApprovalStep.SUPERVISOR].id,
        steps[ApprovalStep.SUPERVISOR].assignee_id or uuid.uuid4(),
        ApprovalDecision.DENIED,
        now=NOW,
    )

    notices = take_kiosk_notices(org.session, driver.id, now=NOW)

    assert [(n.kind, n.channel) for n in notices] == [
        (NotificationKind.REQUEST_DENIED, NotificationChannel.KIOSK)
    ]
    assert take_kiosk_notices(org.session, driver.id, now=NOW) == []


def test_the_wait_counts_from_the_start_of_the_latest_round(org: Org) -> None:
    request = _submit(org)
    org.session.execute(
        update(Approval)
        .where(Approval.request_id == request.id)
        .values(created_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    )
    change_request(
        org.session, org.alice.id, request.reference_code, _draft(*NEXT_WEEK), today=TODAY
    )
    org.session.execute(
        update(Approval)
        .where(Approval.request_id == request.id, Approval.round == 2)
        .values(created_at=datetime(2026, 11, 2, 9, 0, tzinfo=UTC))
    )

    assert send_overdue_notices(org.session, today=date(2026, 11, 4)) == []  # 2 business days


def test_cancelling_a_ceo_request_tells_corporate_hr(org: Org) -> None:
    request = _submit(org, org.ceo)

    cancel_request(org.session, org.ceo.id, request.reference_code, today=TODAY)

    told = {n.recipient_id for n in org.notices(NotificationKind.REQUEST_CANCELLED)}
    assert told == {n.recipient_id for n in org.notices(NotificationKind.CEO_REQUEST)}
    assert org.used(org.ceo) == Decimal(0)
