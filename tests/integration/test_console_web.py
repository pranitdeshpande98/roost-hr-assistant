"""The approver console over HTTP, against real Postgres (FR-21, FR-43, ADR-0013)."""

import hashlib
import re
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolRegistry
from roost.api.app import create_app
from roost.api.services import Services
from roost.api.sessions import CONSOLE_COOKIE_NAME, COOKIE_NAME, load_session, start_session
from roost.auth.console import PasswordRejectedError, set_password
from roost.auth.kiosk import NotAllowedError
from roost.auth.rate_limit import AttemptLimiter
from roost.core.enums import (
    ApprovalStep,
    AuditAction,
    Language,
    LeaveType,
    NotificationKind,
    Role,
    SessionApp,
    VacationRequestStatus,
)
from roost.db.models import (
    Approval,
    AuditLog,
    ConsoleCredential,
    Department,
    Employee,
    EmployeeRole,
    HRReferral,
    LeaveBalance,
    Location,
    Notification,
    VacationRequest,
    WebSession,
)
from roost.guardrails.types import Category
from roost.leave.approvals import hr_team
from roost.leave.repository import create_request
from roost.leave.rules import RequestedDay, VacationRequestDraft
from roost.llm.fake import ScriptedChatModel
from roost.referrals.offer import ReferralDraft
from roost.referrals.service import delete_old_texts, mark_handled, referral_for, send_referral
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

TEMP = "temporary-pass-4471"
OWN = "maple tractor cloud river"
TODAY = date(2026, 11, 2)
WEEK = [date(2027, 1, 11) + timedelta(days=i) for i in range(5)]
SIGN_IN = "/console/sign-in?lang=en"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Browser:
    """One approver's browser on the console."""

    def __init__(self, db: Session, clock: Clock, limiter: AttemptLimiter) -> None:
        self.db = db
        agent = build_graph(
            ScriptedChatModel([]),
            ToolRegistry([]),
            system_prompt=lambda state, today: "sys",
            messages=default_messages(),
            checkpointer=InMemorySaver(),
        )
        services = Services(
            sessions=lambda: nullcontext(db), agent=agent, limiter=limiter, clock=clock
        )
        self.client = TestClient(create_app(services), base_url="https://testserver")

    def sign_in(self, number: str, password: str = TEMP) -> Any:
        return self.client.post(
            "/console/sign-in",
            data={"employee_number": number, "password": password, "language": "en"},
            follow_redirects=False,
        )

    def csrf(self, path: str) -> str:
        match = re.search(r'name="csrf" value="([^"]+)"', self.client.get(path).text)
        assert match is not None, path
        return match.group(1)

    def ready(self, number: str) -> None:
        """Sign in with the temporary password and choose an own one."""
        self.sign_in(number)
        response = self.client.post(
            "/console/change-password",
            data={
                "current_password": TEMP,
                "new_password": OWN,
                "repeat_password": OWN,
                "csrf": self.csrf("/console/change-password"),
            },
            follow_redirects=False,
        )
        assert response.headers["location"] == "/console/queue"

    def decide(self, approval_id: int, decision: str, comment: str = "") -> Any:
        return self.client.post(
            f"/console/approvals/{approval_id}",
            data={
                "decision": decision,
                "comment": comment,
                "csrf": self.csrf(f"/console/approvals/{approval_id}"),
            },
            follow_redirects=False,
        )

    def forge(self, approval_id: int, decision: str = "approve") -> Any:
        """Post a decision without opening the page (it would be refused), with this
        session's real CSRF token: only the routing check stands in the way."""
        token = self.client.cookies[CONSOLE_COOKIE_NAME]
        session = self.db.get_one(WebSession, hashlib.sha256(token.encode()).hexdigest())
        return self.client.post(
            f"/console/approvals/{approval_id}",
            data={"decision": decision, "csrf": session.csrf_token},
            follow_redirects=False,
        )


class Org:
    def __init__(self, db: Session) -> None:
        self.db = db
        alice = db.scalars(
            select(Employee)
            .join(Department)
            .join(Location, Location.id == Employee.location_id)
            .where(Department.name == "Finance", Location.code == "TX_CORP")
            .order_by(Employee.employee_number)
        ).first()
        assert alice is not None
        assert alice.supervisor_id is not None
        self.alice: Employee = alice
        self.supervisor = db.get_one(Employee, alice.supervisor_id)
        self.hr = next(e for e in hr_team(db, alice.location_id) if e.id != self.supervisor.id)
        other_hr = db.scalars(
            select(Employee)
            .join(EmployeeRole)
            .where(
                EmployeeRole.role == Role.HR_GENERALIST,
                Employee.location_id != alice.location_id,
            )
        ).first()
        admin = db.scalars(
            select(Employee).join(EmployeeRole).where(EmployeeRole.role == Role.HR_ADMIN)
        ).first()
        assert other_hr is not None
        assert admin is not None
        self.other_hr: Employee = other_hr
        self.admin: Employee = admin
        worker = db.scalars(
            select(Employee)
            .where(
                Employee.supervisor_id.is_not(None),
                ~select(EmployeeRole).where(EmployeeRole.employee_id == Employee.id).exists(),
            )
            .order_by(Employee.employee_number)
        ).first()
        assert worker is not None
        self.worker: Employee = worker  # no supervisor or HR role
        self.clock = Clock()
        self.limiter = AttemptLimiter()
        self.browsers: list[Browser] = []

    def browser(self) -> Browser:
        browser = Browser(self.db, self.clock, self.limiter)
        browser.client.__enter__()  # runs the app's start-up
        self.browsers.append(browser)
        return browser

    def close(self) -> None:
        for browser in self.browsers:
            browser.client.__exit__(None, None, None)

    def submit(self) -> VacationRequest:
        draft = VacationRequestDraft(tuple(RequestedDay(d) for d in WEEK))
        return create_request(self.db, self.alice.id, draft, today=TODAY)

    def step(self, request: VacationRequest, step: ApprovalStep) -> Approval:
        return self.db.scalars(
            select(Approval).where(Approval.request_id == request.id, Approval.step == step)
        ).one()


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )


@pytest.fixture
def org(db_session: Session, small_org: SeedData) -> Iterator[Org]:
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
    result = Org(db_session)
    # An HR admin hands out temporary passwords (nobody sets their own).
    for person in (result.supervisor, result.hr, result.other_hr):
        set_password(db_session, person.employee_number, TEMP, by=result.admin.id)
    yield result
    result.close()


# --- who may have a console password ---------------------------------------------------


def test_only_supervisors_and_hr_get_a_console_password(org: Org) -> None:
    with pytest.raises(NotAllowedError, match="supervisors and HR"):
        set_password(org.db, org.worker.employee_number, TEMP, by=org.admin.id)


def test_hr_sets_passwords_only_at_their_location_and_never_their_own(org: Org) -> None:
    with pytest.raises(NotAllowedError):
        set_password(org.db, org.supervisor.employee_number, TEMP, by=org.other_hr.id)
    with pytest.raises(NotAllowedError):
        set_password(org.db, org.hr.employee_number, TEMP, by=org.hr.id)
    with pytest.raises(PasswordRejectedError):
        set_password(org.db, org.supervisor.employee_number, "short", by=org.hr.id)

    set_password(org.db, org.supervisor.employee_number, TEMP, by=org.hr.id)

    latest = org.db.scalars(
        select(AuditLog)
        .where(AuditLog.action == AuditAction.PASSWORD_SET)
        .order_by(AuditLog.id.desc())
        .limit(1)
    ).one()
    assert latest.actor_employee_id == org.hr.id
    assert latest.entity_id == str(org.supervisor.id)


# --- sign in ----------------------------------------------------------------------------


def test_first_sign_in_allows_nothing_but_choosing_a_password(org: Org) -> None:
    browser = org.browser()

    response = browser.sign_in(org.supervisor.employee_number)

    assert response.status_code == 303
    assert response.headers["location"] == "/console/change-password"
    cookie = response.headers["set-cookie"]
    assert f"{CONSOLE_COOKIE_NAME}=" in cookie
    assert "Path=/console" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    for path in ("/console/queue", "/console/history", "/console"):
        blocked = browser.client.get(path, follow_redirects=False)
        assert blocked.headers["location"] == "/console/change-password"


def test_a_weak_new_password_is_refused_in_the_page(org: Org) -> None:
    browser = org.browser()
    browser.sign_in(org.supervisor.employee_number)
    weak = f"{org.supervisor.first_name} {org.supervisor.last_name} 2026x"

    response = browser.client.post(
        "/console/change-password",
        data={
            "current_password": TEMP,
            "new_password": weak,
            "repeat_password": weak,
            "csrf": browser.csrf("/console/change-password"),
        },
    )

    assert response.status_code == 400
    assert "Do not use your name or employee number." in response.text


def test_wrong_password_unknown_number_and_non_approver_look_the_same(org: Org) -> None:
    browser = org.browser()
    # The worker has a credential row (say, from before a role was taken away), but no role.
    stored = org.db.get_one(ConsoleCredential, org.supervisor.id).password_hash
    org.db.add(ConsoleCredential(employee_id=org.worker.id, password_hash=stored))
    org.db.flush()

    answers = [
        browser.sign_in(org.supervisor.employee_number, "not the password at all"),
        browser.sign_in("999990"),
        browser.sign_in(org.worker.employee_number),
    ]

    assert {a.status_code for a in answers} == {401}
    assert len({a.text for a in answers}) == 1


def test_five_wrong_passwords_lock_the_account(org: Org) -> None:
    browser = org.browser()
    for _ in range(5):
        response = browser.sign_in(org.supervisor.employee_number, "not the password at all")

    assert response.status_code == 423
    assert browser.sign_in(org.supervisor.employee_number).status_code == 423  # even right
    locked = org.db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.PASSWORD_LOCKED)
    ).one()
    assert locked.entity_id == str(org.supervisor.id)


# --- the queue and decisions ---------------------------------------------------------


def test_each_approver_sees_only_what_is_routed_to_them(org: Org) -> None:
    request = org.submit()
    supervisor, hr, other = org.browser(), org.browser(), org.browser()
    supervisor.ready(org.supervisor.employee_number)
    hr.ready(org.hr.employee_number)
    other.ready(org.other_hr.employee_number)
    hr_step = org.step(request, ApprovalStep.HR)

    assert request.reference_code in supervisor.client.get("/console/queue").text
    assert request.reference_code not in hr.client.get("/console/queue").text  # not yet
    assert hr.client.get(f"/console/approvals/{hr_step.id}").status_code == 404

    done = supervisor.decide(org.step(request, ApprovalStep.SUPERVISOR).id, "approve")

    assert done.headers["location"] == "/console/queue?done=approved"
    assert "approved at your step" in supervisor.client.get(done.headers["location"]).text
    assert request.reference_code in hr.client.get("/console/queue").text
    assert request.reference_code not in other.client.get("/console/queue").text
    assert other.client.get(f"/console/approvals/{hr_step.id}").status_code == 404


def test_an_approver_cannot_decide_what_is_not_routed_to_them(org: Org) -> None:
    request = org.submit()
    supervisor, other = org.browser(), org.browser()
    supervisor.ready(org.supervisor.employee_number)
    other.ready(org.other_hr.employee_number)
    supervisor.decide(org.step(request, ApprovalStep.SUPERVISOR).id, "approve")
    hr_step = org.step(request, ApprovalStep.HR)

    response = other.forge(hr_step.id)  # another location's HR step

    assert response.status_code == 404
    org.db.refresh(hr_step)
    assert hr_step.decided_by_id is None
    assert request.status is VacationRequestStatus.PENDING


def test_hr_denies_with_a_comment_and_the_history_shows_it(org: Org) -> None:
    request = org.submit()
    supervisor, hr = org.browser(), org.browser()
    supervisor.ready(org.supervisor.employee_number)
    hr.ready(org.hr.employee_number)
    supervisor.decide(org.step(request, ApprovalStep.SUPERVISOR).id, "approve")
    hr_step = org.step(request, ApprovalStep.HR)

    page = hr.client.get(f"/console/approvals/{hr_step.id}").text
    assert org.alice.last_name in page
    assert "2027-01-11" in page
    response = hr.decide(hr_step.id, "deny", "Audit week")

    assert response.headers["location"] == "/console/queue?done=denied"
    org.db.refresh(request)
    assert request.status is VacationRequestStatus.DENIED
    assert hr_step.comment == "Audit week"
    history = hr.client.get("/console/history").text
    assert request.reference_code in history
    assert "Denied" in history
    assert request.reference_code not in hr.client.get("/console/queue").text
    assert hr.forge(hr_step.id).status_code == 404  # deciding again is refused


def test_a_decision_without_the_csrf_token_is_refused(org: Org) -> None:
    request = org.submit()
    supervisor = org.browser()
    supervisor.ready(org.supervisor.employee_number)
    step = org.step(request, ApprovalStep.SUPERVISOR)

    response = supervisor.client.post(
        f"/console/approvals/{step.id}", data={"decision": "approve", "csrf": "forged"}
    )

    assert response.status_code == 403
    org.db.refresh(step)
    assert step.decided_by_id is None


def test_console_pages_keep_nothing_and_sign_out_when_idle(org: Org) -> None:
    browser = org.browser()
    browser.ready(org.supervisor.employee_number)

    response = browser.client.get("/console/queue")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert 'data-idle-seconds="900"' in response.text
    assert 'action="/console/sign-out"' in response.text


# --- sessions ---------------------------------------------------------------------------


def test_fifteen_idle_minutes_end_the_session(org: Org) -> None:
    browser = org.browser()
    browser.ready(org.supervisor.employee_number)

    org.clock.now += timedelta(minutes=14, seconds=59)
    assert browser.client.get("/console/queue", follow_redirects=False).status_code == 200
    org.clock.now += timedelta(minutes=15, seconds=1)
    expired = browser.client.get("/console/queue", follow_redirects=False)

    assert expired.headers["location"] == SIGN_IN


def test_a_role_taken_away_ends_console_access_at_once(org: Org) -> None:
    browser = org.browser()
    browser.ready(org.supervisor.employee_number)

    org.db.execute(delete(EmployeeRole).where(EmployeeRole.employee_id == org.supervisor.id))
    response = browser.client.get("/console/queue", follow_redirects=False)

    assert response.headers["location"] == SIGN_IN
    assert browser.sign_in(org.supervisor.employee_number, OWN).status_code == 401


def test_a_console_session_does_not_open_the_kiosk(org: Org) -> None:
    browser = org.browser()
    token = browser.sign_in(org.supervisor.employee_number).cookies[CONSOLE_COOKIE_NAME]
    browser.client.cookies.clear()

    browser.client.cookies.set(COOKIE_NAME, token)
    response = browser.client.get("/chat", follow_redirects=False)

    assert response.headers["location"].startswith("/sign-in")


def test_sign_out_ends_the_session_on_the_server(org: Org) -> None:
    browser = org.browser()
    browser.ready(org.supervisor.employee_number)
    token = browser.client.cookies[CONSOLE_COOKIE_NAME]

    response = browser.client.post("/console/sign-out", follow_redirects=False)

    assert response.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    browser.client.cookies.clear()
    browser.client.cookies.set(CONSOLE_COOKIE_NAME, token, path="/console")
    assert (
        browser.client.get("/console/queue", follow_redirects=False).headers["location"] == SIGN_IN
    )


def test_a_kiosk_session_does_not_open_the_console(org: Org) -> None:
    token, _ = start_session(
        org.db,
        employee_id=org.supervisor.id,
        kiosk_id="plant-kiosk-1",
        language=Language.EN,
        must_change_pin=False,
        now=org.clock(),
    )

    assert load_session(org.db, token, now=org.clock(), app=SessionApp.CONSOLE) is None
    assert load_session(org.db, token, now=org.clock()) is not None  # still a kiosk session


def _fact(page: str, label: str) -> str | None:
    match = re.search(rf"<dt>{re.escape(label)}</dt><dd[^>]*>([^<]*)</dd>", page)
    return match.group(1) if match else None


def test_everyone_sees_the_request_hours_but_only_hr_sees_hours_left(org: Org) -> None:
    request = org.submit()  # 5 days of 8 hours; the employee has 80 hours
    supervisor, hr = org.browser(), org.browser()
    supervisor.ready(org.supervisor.employee_number)
    hr.ready(org.hr.employee_number)
    supervisor_step = org.step(request, ApprovalStep.SUPERVISOR)

    page = supervisor.client.get(f"/console/approvals/{supervisor_step.id}").text
    assert _fact(page, "Hours") == "40"
    assert "Hours left" not in page  # supervisors never see a balance (FR-06)

    supervisor.decide(supervisor_step.id, "approve")
    page = hr.client.get(f"/console/approvals/{org.step(request, ApprovalStep.HR).id}").text
    assert _fact(page, "Hours") == "40"
    assert _fact(page, "Hours left after this request (HR only)") == "40"
    assert "<td>40</td>" in hr.client.get("/console/queue").text


def test_hours_left_counts_the_employees_other_pending_requests(org: Org) -> None:
    org.submit()
    second = create_request(
        org.db,
        org.alice.id,
        VacationRequestDraft((RequestedDay(date(2027, 2, 1)),)),
        today=TODAY,
    )
    supervisor, hr = org.browser(), org.browser()
    supervisor.ready(org.supervisor.employee_number)
    hr.ready(org.hr.employee_number)
    supervisor.decide(org.step(second, ApprovalStep.SUPERVISOR).id, "approve")

    page = hr.client.get(f"/console/approvals/{org.step(second, ApprovalStep.HR).id}").text

    assert _fact(page, "Hours left after this request (HR only)") == "32"  # 80 - 40 - 8


# --- referrals (FR-32) --------------------------------------------------------------

WORDS = "My lead grabbed my arm. <b>Please</b> call me after 3pm."


def _refer(org: Org, employee: Employee | None = None) -> HRReferral:
    return send_referral(
        org.db,
        ReferralDraft((employee or org.alice).id, Category.HARASSMENT, Language.ES, WORDS),
    )


def test_a_referral_tells_hr_nothing_but_that_someone_asked(org: Org) -> None:
    referral = _refer(org)

    notices = org.db.scalars(
        select(Notification).where(Notification.kind == NotificationKind.HR_REFERRAL)
    ).all()
    told = {n.recipient_id for n in notices}
    assert org.hr.id in told
    assert told <= {e.id for e in hr_team(org.db, org.alice.location_id)}
    assert all(n.details == {} for n in notices)
    entry = org.db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.HR_SUMMARY_SENT)
    ).one()
    assert entry.details == {"category": "harassment"}  # never the words
    assert entry.entity_id == str(referral.id)
    assert referral.location_id == org.alice.location_id


def test_hr_reads_the_words_escaped_and_marks_them_handled(org: Org) -> None:
    referral = _refer(org)
    hr = org.browser()
    hr.ready(org.hr.employee_number)

    listing = hr.client.get("/console/referrals").text
    assert 'href="/console/referrals"' in listing  # in the menu for HR
    assert org.alice.last_name in listing
    assert "Harassment" in listing
    page = hr.client.get(f"/console/referrals/{referral.id}").text
    assert "&lt;b&gt;Please&lt;/b&gt; call me after 3pm." in page
    assert 'lang="es"' in page

    done = hr.client.post(
        f"/console/referrals/{referral.id}/handled",
        data={"csrf": hr.csrf(f"/console/referrals/{referral.id}")},
        follow_redirects=False,
    )

    assert done.headers["location"] == "/console/referrals?done=handled"
    org.db.refresh(referral)
    assert referral.handled_by_id == org.hr.id
    assert "Mark as handled" not in hr.client.get(f"/console/referrals/{referral.id}").text
    assert (
        org.db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.HR_REFERRAL_HANDLED))
        .one()
        .actor_employee_id
        == org.hr.id
    )


def test_only_hr_at_the_location_or_an_admin_can_read_a_referral(org: Org) -> None:
    referral = _refer(org)
    supervisor, other = org.browser(), org.browser()
    supervisor.ready(org.supervisor.employee_number)
    other.ready(org.other_hr.employee_number)

    assert 'href="/console/referrals"' not in supervisor.client.get("/console/queue").text
    assert supervisor.client.get("/console/referrals").status_code == 404
    assert supervisor.client.get(f"/console/referrals/{referral.id}").status_code == 404
    assert org.alice.last_name not in other.client.get("/console/referrals").text
    assert other.client.get(f"/console/referrals/{referral.id}").status_code == 404
    forged = other.client.post(
        f"/console/referrals/{referral.id}/handled",
        data={"csrf": other.csrf("/console/change-password")},
    )
    assert forged.status_code == 404
    assert referral_for(org.db, org.admin.id, referral.id) is not None
    assert referral_for(org.db, org.alice.id, referral.id) is None


def test_hr_never_sees_their_own_referral(org: Org) -> None:
    own = _refer(org, org.hr)

    assert referral_for(org.db, org.hr.id, own.id) is None
    assert referral_for(org.db, org.admin.id, own.id) is not None


def test_the_words_are_deleted_90_days_after_handling(org: Org) -> None:
    referral = _refer(org)
    handled = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)
    mark_handled(org.db, org.hr.id, referral.id, now=handled)

    assert delete_old_texts(org.db, now=handled + timedelta(days=89)) == 0
    assert delete_old_texts(org.db, now=handled + timedelta(days=91)) == 1

    org.db.refresh(referral)
    assert referral.text is None
    assert referral.text_deleted_at == handled + timedelta(days=91)
    hr = org.browser()
    hr.ready(org.hr.employee_number)
    assert "deleted 90 days after" in hr.client.get(f"/console/referrals/{referral.id}").text
