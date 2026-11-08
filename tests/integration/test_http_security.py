"""Security through HTTP, against real Postgres (FR-43, NFR-04, NFR-06).

The route tests read the app's own route lists, so a new page or form is covered (and
fails) until it signs people in and checks the CSRF token like every other one.
"""

import re
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolRegistry
from roost.api import console, kiosk
from roost.api.app import SECURITY_HEADERS, create_app
from roost.api.services import Services
from roost.api.sessions import CONSOLE_COOKIE_NAME, COOKIE_NAME
from roost.auth.console import set_password
from roost.auth.kiosk import set_pin
from roost.auth.rate_limit import AttemptLimiter
from roost.core.enums import (
    ApprovalStep,
    Channel,
    EmploymentStatus,
    Language,
    LeaveType,
    Role,
    VacationRequestStatus,
)
from roost.db.models import (
    Approval,
    Department,
    Employee,
    EmployeeRole,
    LeaveBalance,
    Location,
    WebSession,
)
from roost.guardrails.types import Category
from roost.leave.approvals import hr_team
from roost.leave.repository import create_request
from roost.leave.rules import RequestedDay, VacationRequestDraft
from roost.llm.fake import text_reply, tool_reply
from roost.llm.types import ChatMessage, ChatResult, ToolCall, ToolSpec
from roost.referrals.offer import ReferralDraft
from roost.referrals.service import send_referral
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data
from roost.tools.vacation import vacation_tools
from roost.transcripts.service import conversation_for, place_hold, record_turn

pytestmark = pytest.mark.integration

TEMP_PIN, OWN_PIN = "480715", "902614"
TEMP_PASSWORD, OWN_PASSWORD = "temporary-pass-4471", "maple tractor cloud river"
TODAY = date(2026, 11, 2)
MESSAGES = default_messages()
# Pages anyone may open without signing in.
PUBLIC = {"/sign-in", "/console/sign-in", "/health"}
# Event streams answer 204 without a session: EventSource cannot follow a redirect, and
# 204 tells it not to reconnect. Nothing is sent.
STREAMS = {"/chat/stream"}
# Forms that are posted before there is a session, or must always work (idle sign-out).
NO_SESSION_FORMS = {"/sign-in", "/console/sign-in", "/sign-out", "/console/sign-out"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class QueueModel:
    """A scripted model whose replies each test adds after the app is built."""

    name = "fake/queue"

    def __init__(self) -> None:
        self.script: list[ChatResult] = []
        self.requests: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.requests.append(list(messages))
        if not self.script:
            raise AssertionError("no scripted reply left")
        return self.script.pop(0)


def _routes(method: str) -> list[str]:
    paths = {
        route.path
        for router in (kiosk.router, console.router)
        for route in router.routes
        if isinstance(route, APIRoute) and method in route.methods
    }
    return sorted(paths)


def _draft(*days: date) -> VacationRequestDraft:
    return VacationRequestDraft(tuple(RequestedDay(d) for d in days))


class World:
    """Two teammates with kiosk PINs, their supervisor and HR with console passwords,
    and an app whose scripted model each test fills in."""

    def __init__(self, db: Session) -> None:
        self.db = db
        team = db.scalars(
            select(Employee)
            .join(Department)
            .join(Location, Location.id == Employee.location_id)
            .where(Department.name == "Finance", Location.code == "TX_CORP")
            .order_by(Employee.employee_number)
        ).all()
        self.alice, self.bob = team[0], team[1]
        assert self.alice.supervisor_id is not None
        self.supervisor = db.get_one(Employee, self.alice.supervisor_id)
        self.hr = next(e for e in hr_team(db, self.alice.location_id) if e.id != self.supervisor.id)
        admins = db.scalars(
            select(Employee)
            .join(EmployeeRole)
            .where(EmployeeRole.role == Role.HR_ADMIN)
            .order_by(Employee.employee_number)
        ).all()
        admin, self.admin = admins[0], admins[1]
        for person in (self.alice, self.bob):
            set_pin(db, person.employee_number, TEMP_PIN, by=self.hr.id)
        for person in (self.supervisor, self.hr, self.admin):
            set_password(db, person.employee_number, TEMP_PASSWORD, by=admin.id)
        self.clock = Clock()
        self.own_password: set[uuid.UUID] = set()  # who already chose their own
        self.model = QueueModel()
        agent = build_graph(
            self.model,
            ToolRegistry(vacation_tools(lambda: nullcontext(db), MESSAGES)),
            system_prompt=lambda state, today: "sys",
            messages=MESSAGES,
            checkpointer=InMemorySaver(),
            clock=lambda: TODAY,
        )
        self.services = Services(
            sessions=lambda: nullcontext(db),
            agent=agent,
            limiter=AttemptLimiter(max_attempts=1000),
            clock=self.clock,
        )
        self.app = create_app(self.services)
        self.clients: list[TestClient] = []

    def client(self) -> TestClient:
        client = TestClient(self.app, base_url="https://testserver", raise_server_exceptions=False)
        client.__enter__()
        self.clients.append(client)
        return client

    def close(self) -> None:
        for client in self.clients:
            client.__exit__(None, None, None)

    # --- signing in -----------------------------------------------------------------

    def kiosk(self, person: Employee, *, choose_pin: bool = True) -> TestClient:
        client = self.client()
        client.post(
            "/sign-in",
            data={"employee_number": person.employee_number, "pin": TEMP_PIN, "language": "en"},
        )
        if choose_pin:
            client.post(
                "/change-pin",
                data={
                    "current_pin": TEMP_PIN,
                    "new_pin": OWN_PIN,
                    "repeat_pin": OWN_PIN,
                    "csrf": csrf(client, "/change-pin"),
                },
            )
        return client

    def console(self, person: Employee, *, choose_password: bool = True) -> TestClient:
        client = self.client()
        changed = person.id in self.own_password
        client.post(
            "/console/sign-in",
            data={
                "employee_number": person.employee_number,
                "password": OWN_PASSWORD if changed else TEMP_PASSWORD,
            },
        )
        if choose_password and not changed:
            self.own_password.add(person.id)
            client.post(
                "/console/change-password",
                data={
                    "current_password": TEMP_PASSWORD,
                    "new_password": OWN_PASSWORD,
                    "repeat_password": OWN_PASSWORD,
                    "csrf": csrf(client, "/console/change-password"),
                },
            )
        return client

    def thread_of(self, client: TestClient) -> str:
        sessions = self.db.scalars(
            select(WebSession).where(WebSession.employee_id == self.alice.id)
        ).all()
        assert len(sessions) == 1, sessions
        return str(sessions[0].thread_id)

    def tool_results(self, thread: str) -> list[str]:
        state = self.services.agent.get_state({"configurable": {"thread_id": thread}})
        return [m.content or "" for m in state.values["messages"] if m.role == "tool"]


def csrf(client: TestClient, path: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert match is not None, path
    return match.group(1)


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )


@pytest.fixture
def world(db_session: Session, small_org: SeedData) -> Any:
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
    result = World(db_session)
    yield result
    result.close()


# --- every page and form ------------------------------------------------------------


def _fill(path: str) -> str:
    """A route with its path parameters filled in (ids that exist nowhere)."""
    return path.replace("{approval_id}", "999999").replace("{referral_id}", str(uuid.uuid4()))


def test_every_page_needs_a_signed_in_session(world: World) -> None:
    client = world.client()
    pages = [p for p in _routes("GET") if p not in PUBLIC | STREAMS]
    assert len(pages) >= 9  # the kiosk and console pages, read from the app

    for path in STREAMS:
        response = client.get(path)
        assert (response.status_code, response.content) == (204, b""), path

    for path in pages:
        response = client.get(_fill(path), follow_redirects=False)
        sign_in = "/console/sign-in" if path.startswith("/console") else "/sign-in"
        assert response.status_code == 303, path
        assert response.headers["location"].startswith(sign_in), path


def test_every_form_needs_the_sessions_csrf_token(world: World) -> None:
    request = create_request(world.db, world.alice.id, _draft(date(2027, 1, 11)), today=TODAY)
    referral = send_referral(
        world.db, ReferralDraft(world.alice.id, Category.HARASSMENT, Language.EN, "Help.")
    )
    step = world.db.scalars(
        select(Approval).where(
            Approval.request_id == request.id, Approval.step == ApprovalStep.SUPERVISOR
        )
    ).one()
    unheld, held = (
        conversation_for(
            world.db,
            thread_id=uuid.uuid4(),
            employee_id=world.alice.id,
            channel=Channel.KIOSK,
            language=Language.EN,
            now=world.clock(),
        )
        for _ in range(2)
    )
    for conversation in (unheld, held):
        record_turn(
            world.db,
            conversation,
            said="hi",
            reply="Hello.",
            kind="answer",
            category=None,
            resumed=False,
            now=world.clock(),
        )
    assert place_hold(world.db, world.admin.id, held.id, reason="case", now=world.clock())
    # Per form: who must be signed in, the fields, and a real target if it acts on one.
    forms: dict[str, tuple[Callable[[], TestClient], dict[str, str]]] = {
        "/change-pin": (lambda: world.kiosk(world.alice, choose_pin=False), {}),
        "/chat/messages": (lambda: world.kiosk(world.bob), {"text": "hello"}),
        "/console/change-password": (
            lambda: world.console(world.supervisor, choose_password=False),
            {},
        ),
        "/console/approvals/{approval_id}": (
            lambda: world.console(world.supervisor),
            {"decision": "approve"},
        ),
        "/console/referrals/{referral_id}/handled": (lambda: world.console(world.hr), {}),
        "/console/transcripts/{conversation_id}/hold": (
            lambda: world.console(world.admin),
            {"reason": "case"},
        ),
        "/console/transcripts/{conversation_id}/release": (lambda: world.console(world.admin), {}),
    }
    assert set(forms) == {p for p in _routes("POST") if p not in NO_SESSION_FORMS}

    for template, (sign_in, data) in forms.items():
        path = template.replace("{approval_id}", str(step.id))
        path = path.replace("{referral_id}", str(referral.id))
        target = held if template.endswith("/release") else unheld
        path = path.replace("{conversation_id}", str(target.id))
        client = sign_in()
        response = client.post(path, data={**data, "csrf": "forged"}, follow_redirects=False)
        assert response.status_code == 403, path

    world.db.refresh(step)
    world.db.refresh(referral)
    world.db.refresh(unheld)
    world.db.refresh(held)
    assert step.decided_by_id is None
    assert referral.handled_at is None
    assert (unheld.legal_hold, held.legal_hold) == (False, True)


def test_every_kind_of_response_carries_the_security_headers(world: World) -> None:
    client = world.client()
    responses = [
        client.get("/sign-in"),
        client.get("/console/sign-in"),
        client.get("/health"),
        client.get("/static/roost.css"),
        client.get("/no-such-page"),
        client.get("/chat/messages"),  # wrong method
        client.get("/chat", follow_redirects=False),
    ]

    for response in responses:
        for name, value in SECURITY_HEADERS.items():
            assert response.headers.get(name) == value, (response.url, name)


def test_wrong_methods_bad_paths_and_oversized_input_are_refused(world: World) -> None:
    client = world.kiosk(world.alice)

    assert client.get("/chat/messages").status_code == 405
    assert client.get("/static/../config/ui.yaml").status_code == 404
    assert client.get("/static/%2e%2e/config/ui.yaml").status_code == 404
    too_long = client.post(
        "/chat/messages", data={"text": "x" * 1001, "csrf": csrf(client, "/chat")}
    )
    assert too_long.status_code == 422
    assert world.model.requests == []  # nothing reached the model
    # No interactive API documentation is served.
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_an_unexpected_error_shows_no_details(world: World) -> None:
    client = world.client()

    def broken() -> datetime:
        raise RuntimeError("secret detail: postgresql://roost:hunter2@db")

    world.services.clock = broken
    client.cookies.set(COOKIE_NAME, "anything")
    response = client.get("/sign-in")

    assert response.status_code == 500
    assert "secret detail" not in response.text
    assert "Traceback" not in response.text


# --- whose data -----------------------------------------------------------------------


def test_an_employee_reaches_only_their_own_requests(world: World) -> None:
    mine = create_request(world.db, world.alice.id, _draft(date(2027, 1, 11)), today=TODAY)
    bobs = create_request(world.db, world.bob.id, _draft(date(2027, 2, 1)), today=TODAY)
    world.model.script.extend(
        [
            tool_reply(ToolCall(id="c1", name="list_my_vacation_requests", arguments={})),
            tool_reply(
                ToolCall(
                    id="c2",
                    name="cancel_vacation_request",
                    arguments={"reference_code": bobs.reference_code},
                )
            ),
            text_reply("Done."),
        ]
    )
    client = world.kiosk(world.alice)

    # An employee id slipped into the form is ignored: it comes only from the session.
    client.post(
        "/chat/messages",
        data={
            "text": f"list my requests and cancel {bobs.reference_code}",
            "employee_id": str(world.bob.id),
            "csrf": csrf(client, "/chat"),
        },
    )

    listed, cancelled = world.tool_results(world.thread_of(client))
    assert mine.reference_code in listed
    assert bobs.reference_code not in listed
    assert cancelled == f"Error: no request {bobs.reference_code} for this employee."
    world.db.refresh(bobs)
    assert bobs.status is VacationRequestStatus.PENDING


def test_a_supervisor_of_another_team_sees_nothing(world: World) -> None:
    request = create_request(world.db, world.alice.id, _draft(date(2027, 1, 11)), today=TODAY)
    step = world.db.scalars(
        select(Approval).where(
            Approval.request_id == request.id, Approval.step == ApprovalStep.SUPERVISOR
        )
    ).one()
    admin = world.db.scalars(
        select(Employee).join(EmployeeRole).where(EmployeeRole.role == Role.HR_ADMIN)
    ).first()
    assert admin is not None
    hr_people = select(EmployeeRole.employee_id).where(
        EmployeeRole.role.in_([Role.HR_ADMIN, Role.HR_GENERALIST])
    )
    other = world.db.scalars(
        select(Employee)
        .join(EmployeeRole)
        .where(
            EmployeeRole.role == Role.SUPERVISOR,
            Employee.id != world.supervisor.id,
            Employee.id.not_in(hr_people),  # a plain supervisor of another team
        )
        .order_by(Employee.employee_number)
    ).first()
    assert other is not None
    set_password(world.db, other.employee_number, TEMP_PASSWORD, by=admin.id)
    client = world.console(other)

    assert request.reference_code not in client.get("/console/queue").text
    assert client.get(f"/console/approvals/{step.id}").status_code == 404
    assert request.reference_code not in client.get("/console/history").text


# --- sessions -------------------------------------------------------------------------


def test_a_terminated_employee_is_signed_out_of_the_kiosk_at_once(world: World) -> None:
    client = world.kiosk(world.alice)
    assert client.get("/chat", follow_redirects=False).status_code == 200

    world.db.execute(
        update(Employee)
        .where(Employee.id == world.alice.id)
        .values(employment_status=EmploymentStatus.TERMINATED)
    )

    response = client.get("/chat", follow_redirects=False)
    assert response.headers["location"].startswith("/sign-in")
    assert (
        world.db.scalars(select(WebSession).where(WebSession.employee_id == world.alice.id)).all()
        == []
    )


@pytest.mark.parametrize("app", ["kiosk", "console"])
def test_choosing_a_pin_or_password_replaces_the_session_token(world: World, app: str) -> None:
    if app == "kiosk":
        client = world.kiosk(world.alice, choose_pin=False)
        name, page, form, fields = (
            COOKIE_NAME,
            "/chat",
            "/change-pin",
            {"current_pin": TEMP_PIN, "new_pin": OWN_PIN, "repeat_pin": OWN_PIN},
        )
    else:
        client = world.console(world.supervisor, choose_password=False)
        name, page, form, fields = (
            CONSOLE_COOKIE_NAME,
            "/console/queue",
            "/console/change-password",
            {
                "current_password": TEMP_PASSWORD,
                "new_password": OWN_PASSWORD,
                "repeat_password": OWN_PASSWORD,
            },
        )
    old = client.cookies[name]

    client.post(form, data={**fields, "csrf": csrf(client, form)})

    new = client.cookies[name]
    assert new != old
    assert client.get(page, follow_redirects=False).status_code == 200
    stolen = world.client()
    stolen.cookies.set(name, old, path="/console" if app == "console" else "/")
    assert stolen.get(page, follow_redirects=False).status_code == 303


def test_an_idle_kiosk_session_is_gone_from_the_database_after_the_next_sign_in(
    world: World,
) -> None:
    world.kiosk(world.alice)
    world.clock.now += timedelta(minutes=3)

    world.kiosk(world.bob, choose_pin=False)

    assert (
        world.db.scalars(select(WebSession).where(WebSession.employee_id == world.alice.id)).all()
        == []
    )
