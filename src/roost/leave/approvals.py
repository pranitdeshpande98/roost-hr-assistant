"""Who approves a vacation request, and what each decision does (FR-20, FR-21, FR-13,
FR-14, FR-23; business rules section 4).

Plant employees: supervisor, then plant HR. Corporate staff: supervisor, then Corporate
HR. That is "the HR team at the employee's location" in both cases. The CEO has no
supervisor: the request is approved at once and Corporate HR is told.

Every function works in the caller's transaction and writes the audit log and the
notification outbox there, so a decision, its audit entry and its notifications are
saved together.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from roost.audit import log as audit_log
from roost.core.enums import (
    ApprovalDecision,
    ApprovalStep,
    AuditAction,
    EmploymentStatus,
    LocationKind,
    NotificationKind,
    Role,
    VacationRequestStatus,
)
from roost.db.models import (
    Approval,
    Employee,
    EmployeeRole,
    Holiday,
    Location,
    Notification,
    VacationRequest,
)
from roost.leave.balance import post_hours, release_hours
from roost.notifications.outbox import notify

# FR-23: no decision within 3 business days, or 2 days before the start date.
OVERDUE_BUSINESS_DAYS = 3
OVERDUE_DAYS_BEFORE_START = 2


class ApprovalError(ValueError):
    """The approval cannot be decided now: already decided, superseded, or out of order."""


class NotYourApprovalError(PermissionError):
    """The person deciding may not decide this approval."""


@dataclass(frozen=True, slots=True)
class PendingApproval:
    approval: Approval
    request: VacationRequest
    employee: Employee


# --- who is who ------------------------------------------------------------------------


def _roles(session: Session, employee_id: uuid.UUID) -> set[Role]:
    return set(
        session.scalars(select(EmployeeRole.role).where(EmployeeRole.employee_id == employee_id))
    )


def hr_team(session: Session, location_id: int) -> list[Employee]:
    """Active HR generalists at a location."""
    return list(
        session.scalars(
            select(Employee)
            .join(EmployeeRole)
            .where(
                EmployeeRole.role == Role.HR_GENERALIST,
                Employee.location_id == location_id,
                Employee.employment_status == EmploymentStatus.ACTIVE,
            )
            .order_by(Employee.employee_number)
        )
    )


def _corporate_location_id(session: Session) -> int:
    return session.scalars(select(Location.id).where(Location.kind == LocationKind.CORPORATE)).one()


def current_round(session: Session, request_id: uuid.UUID) -> int:
    """The latest approval round of a request (0 before the first)."""
    return (
        session.scalar(
            select(func.coalesce(func.max(Approval.round), 0)).where(
                Approval.request_id == request_id
            )
        )
        or 0
    )


def _audit(
    session: Session,
    action: AuditAction,
    actor: uuid.UUID | None,
    request: VacationRequest,
    **details: object,
) -> None:
    audit_log.record(
        session,
        action,
        actor=actor,
        entity_type="vacation_request",
        entity_id=str(request.id),
        details={"reference": request.reference_code, **details},
    )


# --- starting, deciding, cancelling ----------------------------------------------------


def start_approval(session: Session, request: VacationRequest) -> None:
    """A submitted or changed request starts a new approval round (FR-13, FR-20)."""
    employee = session.get_one(Employee, request.employee_id)
    if employee.supervisor_id is None:  # the CEO: no approval needed
        request.status = VacationRequestStatus.APPROVED
        post_hours(session, request)
        _audit(session, AuditAction.REQUEST_APPROVED, None, request, route="no_approval_needed")
        for hr in hr_team(session, _corporate_location_id(session)):
            notify(session, hr, NotificationKind.CEO_REQUEST, request)
        session.flush()
        return

    next_round = current_round(session, request.id) + 1
    session.add_all(
        [
            Approval(
                request_id=request.id,
                round=next_round,
                step=ApprovalStep.SUPERVISOR,
                assignee_id=employee.supervisor_id,
            ),
            Approval(
                request_id=request.id,
                round=next_round,
                step=ApprovalStep.HR,
                hr_location_id=employee.location_id,
            ),
        ]
    )
    notify(
        session,
        session.get_one(Employee, employee.supervisor_id),
        NotificationKind.APPROVAL_NEEDED,
        request,
    )
    session.flush()


def _may_decide(session: Session, approval: Approval, approver: Employee) -> bool:
    if approval.step is ApprovalStep.SUPERVISOR:
        return approval.assignee_id == approver.id
    roles = _roles(session, approver.id)
    return Role.HR_ADMIN in roles or (
        Role.HR_GENERALIST in roles and approver.location_id == approval.hr_location_id
    )


def decide(
    session: Session,
    approval_id: int,
    approver_id: uuid.UUID,
    decision: ApprovalDecision,
    *,
    comment: str | None = None,
    now: datetime,
) -> VacationRequest:
    """Approve or deny one step (FR-21). Raises NotYourApprovalError or ApprovalError."""
    if decision is ApprovalDecision.PENDING:
        raise ApprovalError("a decision is approved or denied")
    approval = session.get(Approval, approval_id, with_for_update=True)
    if approval is None:
        raise ApprovalError("no such approval")
    # Locked: two approvers deciding at the same moment cannot both win.
    request = session.get_one(VacationRequest, approval.request_id, with_for_update=True)
    approver = session.get(Employee, approver_id)
    if approver is None or not _may_decide(session, approval, approver):
        raise NotYourApprovalError("not assigned to this approval")
    if approver.id == request.employee_id:
        raise NotYourApprovalError("nobody approves their own request")
    if (
        request.status is not VacationRequestStatus.PENDING
        or approval.decision is not ApprovalDecision.PENDING
    ):
        raise ApprovalError("already decided")
    if approval.round != current_round(session, request.id):
        raise ApprovalError("the request changed since; decide the new version")
    if approval.step is ApprovalStep.HR:
        supervisor_step = session.scalars(
            select(Approval).where(
                Approval.request_id == request.id,
                Approval.round == approval.round,
                Approval.step == ApprovalStep.SUPERVISOR,
            )
        ).one()
        if supervisor_step.decision is not ApprovalDecision.APPROVED:
            raise ApprovalError("the supervisor decides first")

    approval.decision = decision
    approval.decided_by_id = approver.id
    approval.decided_at = now
    approval.comment = (comment or "").strip()[:500] or None
    _audit(
        session,
        AuditAction.APPROVAL_DECIDED,
        approver.id,
        request,
        step=approval.step.value,
        decision=decision.value,
        round=approval.round,
    )

    employee = session.get_one(Employee, request.employee_id)
    extra = {"comment": approval.comment} if approval.comment else {}
    if decision is ApprovalDecision.DENIED:
        request.status = VacationRequestStatus.DENIED
        _audit(session, AuditAction.REQUEST_DENIED, approver.id, request, step=approval.step.value)
        notify(session, employee, NotificationKind.REQUEST_DENIED, request, extra)
    elif approval.step is ApprovalStep.SUPERVISOR:
        for hr in hr_team(session, approval.hr_location_id or employee.location_id):
            if hr.id != employee.id:
                notify(session, hr, NotificationKind.APPROVAL_NEEDED, request)
    else:
        request.status = VacationRequestStatus.APPROVED
        post_hours(session, request)
        _audit(session, AuditAction.REQUEST_APPROVED, approver.id, request)
        notify(session, employee, NotificationKind.REQUEST_APPROVED, request, extra)
    session.flush()
    return request


def notify_cancellation(session: Session, request: VacationRequest, *, was_approved: bool) -> None:
    """FR-14: the approvers who were involved hear that the request was cancelled: the
    supervisor always, the HR team once the request reached them. An approved request
    gives its hours back."""
    if was_approved:
        release_hours(session, request)
    employee = session.get_one(Employee, request.employee_id)
    recipients: dict[uuid.UUID, Employee] = {}
    if employee.supervisor_id is None:  # the CEO: Corporate HR was told, so tell them again
        recipients = {hr.id: hr for hr in hr_team(session, _corporate_location_id(session))}
    approvals = session.scalars(
        select(Approval).where(
            Approval.request_id == request.id,
            Approval.round == current_round(session, request.id),
        )
    ).all()
    reached_hr = was_approved or any(a.decision is ApprovalDecision.APPROVED for a in approvals)
    for approval in approvals:
        if approval.step is ApprovalStep.SUPERVISOR and approval.assignee_id is not None:
            recipients[approval.assignee_id] = session.get_one(Employee, approval.assignee_id)
        elif approval.step is ApprovalStep.HR and approval.hr_location_id and reached_hr:
            recipients |= {hr.id: hr for hr in hr_team(session, approval.hr_location_id)}
    recipients.pop(request.employee_id, None)
    for recipient in recipients.values():
        notify(session, recipient, NotificationKind.REQUEST_CANCELLED, request)


# --- the approver's queue ----------------------------------------------------------------


def pending_for(session: Session, approver_id: uuid.UUID) -> list[PendingApproval]:
    """What this approver can decide now (FR-43: only what is routed to them)."""
    approver = session.get(Employee, approver_id)
    if approver is None:
        return []
    roles = _roles(session, approver_id)
    latest = (
        select(Approval.request_id, func.max(Approval.round).label("round"))
        .group_by(Approval.request_id)
        .subquery()
    )
    supervisor_step = aliased(Approval)
    routed = and_(Approval.step == ApprovalStep.SUPERVISOR, Approval.assignee_id == approver_id)
    if roles & {Role.HR_ADMIN, Role.HR_GENERALIST}:
        supervisor_approved = exists().where(
            supervisor_step.request_id == Approval.request_id,
            supervisor_step.round == Approval.round,
            supervisor_step.step == ApprovalStep.SUPERVISOR,
            supervisor_step.decision == ApprovalDecision.APPROVED,
        )
        hr_step = and_(Approval.step == ApprovalStep.HR, supervisor_approved)
        if Role.HR_ADMIN not in roles:  # a generalist decides for their own location
            hr_step = and_(hr_step, Approval.hr_location_id == approver.location_id)
        routed = or_(routed, hr_step)
    rows = session.execute(
        select(Approval, VacationRequest, Employee)
        .join(
            latest,
            and_(latest.c.request_id == Approval.request_id, latest.c.round == Approval.round),
        )
        .join(VacationRequest, VacationRequest.id == Approval.request_id)
        .join(Employee, Employee.id == VacationRequest.employee_id)
        .where(
            Approval.decision == ApprovalDecision.PENDING,
            VacationRequest.status == VacationRequestStatus.PENDING,
            VacationRequest.employee_id != approver_id,
            routed,
        )
        .order_by(Approval.created_at, Approval.id)
    ).all()
    return [PendingApproval(a, r, e) for a, r, e in rows]


# --- FR-23: tell the employee to check with HR -------------------------------------------


def _business_days_between(session: Session, start: date, end: date) -> int:
    """Weekdays after `start` up to and including `end`, not counting company holidays."""
    holidays = set(
        session.scalars(
            select(Holiday.observed_date).where(
                Holiday.observed_date > start, Holiday.observed_date <= end
            )
        )
    )
    days = (start + timedelta(days=n) for n in range(1, (end - start).days + 1))
    return sum(1 for d in days if d.weekday() < 5 and d not in holidays)


def send_overdue_notices(session: Session, *, today: date) -> list[VacationRequest]:
    """FR-23: once per round, tell the employee to check with HR in person when no
    decision came within 3 business days, or the first day is 2 days away. There is no
    automatic escalation. Returns the requests that got a notice."""
    rounds = (
        select(
            Approval.request_id,
            Approval.round,
            func.min(Approval.created_at).label("started"),
        )
        .group_by(Approval.request_id, Approval.round)
        .subquery()
    )
    latest = (
        select(Approval.request_id, func.max(Approval.round).label("round"))
        .group_by(Approval.request_id)
        .subquery()
    )
    candidates = session.execute(  # the start of each pending request's latest round
        select(VacationRequest, rounds.c.round, rounds.c.started)
        .join(rounds, rounds.c.request_id == VacationRequest.id)
        .join(
            latest,
            and_(latest.c.request_id == rounds.c.request_id, latest.c.round == rounds.c.round),
        )
        .where(VacationRequest.status == VacationRequestStatus.PENDING)
    ).all()
    notified = []
    for request, round_, started in candidates:
        first_day = min(d.day for d in request.days)
        waited = _business_days_between(session, started.date(), today)
        if waited < OVERDUE_BUSINESS_DAYS and (first_day - today).days > OVERDUE_DAYS_BEFORE_START:
            continue
        already = session.scalar(
            select(
                exists().where(
                    Notification.request_id == request.id,
                    Notification.kind == NotificationKind.DECISION_OVERDUE,
                    Notification.details["round"].as_integer() == round_,
                )
            )
        )
        if already:
            continue
        employee = session.get_one(Employee, request.employee_id)
        notify(session, employee, NotificationKind.DECISION_OVERDUE, request, {"round": round_})
        notified.append(request)
    session.flush()
    return notified
