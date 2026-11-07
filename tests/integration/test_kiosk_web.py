"""The kiosk over HTTP, against real Postgres: sign-in, first PIN, chat, idle, sign-out."""

import re
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roost.agent.confirmation import Confirmation
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.api.app import create_app
from roost.api.services import Services
from roost.api.sessions import COOKIE_NAME
from roost.auth.kiosk import set_pin
from roost.auth.rate_limit import AttemptLimiter
from roost.core.enums import LocationKind, NotificationChannel, NotificationKind, Role
from roost.db.models import Employee, EmployeeRole, Location, Notification, WebSession
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall, ToolSpec
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

TEMP_PIN = "480715"
OWN_PIN = "902614"
MESSAGES = default_messages()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class SubmitTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="submit", description="Submit.", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        reply = context.confirm("Submit Monday? Reply YES or NO.")
        return "SUBMITTED." if reply.answer is Confirmation.YES else "NOT SUBMITTED."


class Kiosk:
    """A test client for one kiosk, with the scripted model and the clock it uses."""

    def __init__(
        self, db: Session, worker: Employee, replies: list[Any], limiter: AttemptLimiter
    ) -> None:
        self.db = db
        self.worker = worker
        self.clock = Clock()
        self.model = ScriptedChatModel(replies)
        agent = build_graph(
            self.model,
            ToolRegistry([SubmitTool()]),
            system_prompt=lambda state, today: "sys",
            messages=MESSAGES,
            checkpointer=InMemorySaver(),
        )
        services = Services(
            sessions=lambda: nullcontext(db), agent=agent, limiter=limiter, clock=self.clock
        )
        self.client = TestClient(create_app(services), base_url="https://testserver")

    def sign_in(self, pin: str = TEMP_PIN, number: str | None = None, lang: str = "en") -> Any:
        return self.client.post(
            "/sign-in",
            data={
                "employee_number": number or self.worker.employee_number,
                "pin": pin,
                "language": lang,
            },
            follow_redirects=False,
        )

    def csrf(self, path: str = "/chat") -> str:
        match = re.search(r'name="csrf" value="([^"]+)"', self.client.get(path).text)
        assert match is not None
        return match.group(1)

    def choose_pin(self) -> None:
        self.sign_in()
        self.client.post(
            "/change-pin",
            data={
                "current_pin": TEMP_PIN,
                "new_pin": OWN_PIN,
                "repeat_pin": OWN_PIN,
                "csrf": self.csrf("/change-pin"),
            },
        )

    def say(self, text: str, htmx: bool = True) -> Any:
        headers = {"HX-Request": "true"} if htmx else {}
        return self.client.post(
            "/chat/messages",
            data={"text": text, "csrf": self.csrf()},
            headers=headers,
            follow_redirects=False,
        )


@pytest.fixture
def org(db_session: Session) -> tuple[Session, Employee]:
    data = generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )
    load_seed_data(db_session, data, holiday_years=(2026,))
    hr = db_session.scalars(
        select(Employee)
        .join(EmployeeRole)
        .join(Location, Location.id == Employee.location_id)
        .where(EmployeeRole.role == Role.HR_GENERALIST, Location.kind == LocationKind.PLANT)
        .limit(1)
    ).one()
    worker = db_session.scalars(
        select(Employee)
        .where(Employee.location_id == hr.location_id, Employee.id != hr.id)
        .limit(1)
    ).one()
    set_pin(db_session, worker.employee_number, TEMP_PIN, by=hr.id)
    return db_session, worker


@pytest.fixture
def kiosk(org: tuple[Session, Employee]) -> Iterator[Kiosk]:
    db, worker = org
    replies = [
        text_reply("You get **10 days** a year [§6.1]."),
        text_reply("Fourteen days [§6.4]."),
    ]
    instance = Kiosk(db, worker, replies, AttemptLimiter())
    with instance.client:
        yield instance


def test_every_page_tells_the_browser_to_keep_nothing(kiosk: Kiosk) -> None:
    page = kiosk.client.get("/sign-in?lang=vi")

    assert page.status_code == 200
    assert "Mã số nhân viên" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert '"historyCacheSize": 0' in page.text  # htmx keeps no page copies


def test_a_wrong_pin_and_an_unknown_number_look_the_same(kiosk: Kiosk) -> None:
    wrong = kiosk.sign_in(pin="000001")
    unknown = kiosk.sign_in(number="999999")

    assert wrong.status_code == unknown.status_code == 401
    assert "do not match" in wrong.text
    assert wrong.text == unknown.text
    assert COOKIE_NAME not in wrong.cookies


def test_first_sign_in_allows_nothing_but_choosing_a_pin(kiosk: Kiosk) -> None:
    signed_in = kiosk.sign_in()
    assert signed_in.headers["location"] == "/change-pin"
    assert kiosk.client.get("/chat", follow_redirects=False).headers["location"] == "/change-pin"

    csrf = kiosk.csrf("/change-pin")
    weak = kiosk.client.post(
        "/change-pin",
        data={"current_pin": TEMP_PIN, "new_pin": "123456", "repeat_pin": "123456", "csrf": csrf},
    )
    assert weak.status_code == 400
    assert "is a sequence" in weak.text
    differ = kiosk.client.post(
        "/change-pin",
        data={"current_pin": TEMP_PIN, "new_pin": OWN_PIN, "repeat_pin": "902615", "csrf": csrf},
    )
    assert "are different" in differ.text

    done = kiosk.client.post(
        "/change-pin",
        data={"current_pin": TEMP_PIN, "new_pin": OWN_PIN, "repeat_pin": OWN_PIN, "csrf": csrf},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/chat"
    assert kiosk.client.get("/chat").status_code == 200


def test_the_cookie_is_a_secret_the_server_keeps_only_as_a_hash(kiosk: Kiosk) -> None:
    response = kiosk.sign_in()
    header = response.headers["set-cookie"].lower()

    assert "httponly" in header
    assert "secure" in header
    assert "samesite=strict" in header
    assert "max-age" not in header  # a session cookie: gone when the browser closes
    assert "expires" not in header
    token = response.cookies[COOKIE_NAME]
    stored = kiosk.db.scalars(select(WebSession.token_hash)).one()
    assert stored != token
    assert len(stored) == 64


def test_chat_shows_the_reply_as_plain_text_with_the_footer(kiosk: Kiosk) -> None:
    kiosk.choose_pin()

    fragment = kiosk.say("How many vacation days do I get?")

    assert fragment.status_code == 200
    assert fragment.text.lstrip().startswith('<section id="conversation"')  # htmx swaps this in
    assert "How many vacation days do I get?" in fragment.text
    assert "You get 10 days a year." in fragment.text  # the ** are gone
    assert "[§6.1]" not in fragment.text  # cited, but no tool returned it: removed
    assert "not an HR decision" in fragment.text


def test_without_javascript_a_message_returns_to_the_page(kiosk: Kiosk) -> None:
    kiosk.choose_pin()

    sent = kiosk.say("How many vacation days do I get?", htmx=False)

    assert (sent.status_code, sent.headers["location"]) == (303, "/chat")
    assert "You get 10 days a year" in kiosk.client.get("/chat").text


def test_a_form_without_the_sessions_csrf_token_is_refused(kiosk: Kiosk) -> None:
    kiosk.choose_pin()

    forged = kiosk.client.post("/chat/messages", data={"text": "hi", "csrf": "forged"})

    assert forged.status_code == 403
    assert kiosk.model.requests == []


def test_two_idle_minutes_end_the_session_on_the_server(kiosk: Kiosk) -> None:
    kiosk.choose_pin()
    kiosk.clock.now += timedelta(minutes=1, seconds=59)
    assert (
        kiosk.client.get("/chat", follow_redirects=False).status_code == 200
    )  # activity resets it

    kiosk.clock.now += timedelta(minutes=2, seconds=1)
    expired = kiosk.client.get("/chat", follow_redirects=False)

    assert expired.headers["location"].startswith("/sign-in")
    assert kiosk.db.scalar(select(func.count()).select_from(WebSession)) == 0


def test_an_htmx_request_after_expiry_replaces_the_whole_screen(kiosk: Kiosk) -> None:
    kiosk.choose_pin()
    csrf = kiosk.csrf()
    kiosk.clock.now += timedelta(minutes=3)

    late = kiosk.client.post(
        "/chat/messages", data={"text": "hi", "csrf": csrf}, headers={"HX-Request": "true"}
    )

    assert late.headers["hx-redirect"].startswith("/sign-in")
    assert kiosk.model.requests == []


def test_sign_out_clears_the_browser_and_the_old_cookie_stops_working(kiosk: Kiosk) -> None:
    kiosk.choose_pin()
    token = kiosk.client.cookies[COOKIE_NAME]

    out = kiosk.client.post("/sign-out", follow_redirects=False)

    assert out.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    kiosk.client.cookies.set(COOKIE_NAME, token)  # replay the old cookie
    assert (
        kiosk.client.get("/chat", follow_redirects=False).headers["location"].startswith("/sign-in")
    )


def test_five_wrong_pins_lock_the_account(kiosk: Kiosk) -> None:
    for _ in range(4):
        assert kiosk.sign_in(pin="000001").status_code == 401

    locked = kiosk.sign_in(pin="000001")
    right_pin = kiosk.sign_in()

    assert locked.status_code == right_pin.status_code == 423
    assert "try again in 15 minute(s)" in locked.text


def test_one_kiosk_cannot_keep_trying_numbers(org: tuple[Session, Employee]) -> None:
    db, worker = org
    kiosk = Kiosk(db, worker, [], AttemptLimiter(max_attempts=3))
    with kiosk.client:
        tries = [kiosk.sign_in(pin="000001", number=f"99999{i}").status_code for i in range(4)]
        other_kiosk = kiosk.client.post(
            "/sign-in",
            data={"employee_number": "999990", "pin": "000001"},
            headers={"X-Kiosk-Id": "plant-2-kiosk-b"},
        )

    assert tries == [401, 401, 401, 429]
    assert other_kiosk.status_code == 401  # each kiosk has its own count


def test_a_confirmation_gets_yes_and_no_buttons_in_the_employees_language(
    org: tuple[Session, Employee],
) -> None:
    db, worker = org
    replies = [tool_reply(ToolCall(id="c1", name="submit", arguments={})), text_reply("Sent.")]
    kiosk = Kiosk(db, worker, replies, AttemptLimiter())
    with kiosk.client:
        kiosk.sign_in(lang="es")
        kiosk.client.post(
            "/change-pin",
            data={
                "current_pin": TEMP_PIN,
                "new_pin": OWN_PIN,
                "repeat_pin": OWN_PIN,
                "csrf": kiosk.csrf("/change-pin"),
            },
        )
        asked = kiosk.say("Quiero el lunes libre")
        assert 'value="SÍ"' in asked.text
        assert 'value="NO"' in asked.text

        answered = kiosk.say("SÍ")

    assert "Sent." in answered.text
    assert 'value="SÍ"' not in answered.text


def test_health_reports_the_database(kiosk: Kiosk) -> None:
    assert kiosk.client.get("/health").json() == {"status": "ok"}


def test_abandoned_sessions_are_swept_at_the_next_sign_in(kiosk: Kiosk) -> None:
    kiosk.choose_pin()
    kiosk.client.cookies.clear()  # walked away without signing out
    kiosk.clock.now += timedelta(minutes=5)

    # From another kiosk: ending the employee's own session there would not touch it.
    kiosk.client.post(
        "/sign-in",
        data={"employee_number": kiosk.worker.employee_number, "pin": OWN_PIN},
        headers={"X-Kiosk-Id": "plant-2-kiosk-b"},
    )

    sessions = kiosk.db.scalars(select(WebSession)).all()
    assert len(sessions) == 1
    assert sessions[0].last_seen_at == kiosk.clock.now  # only the new one is left


def test_kiosk_notices_show_once_in_the_employees_language(kiosk: Kiosk) -> None:
    kiosk.choose_pin()
    kiosk.db.add(
        Notification(
            recipient_id=kiosk.worker.id,
            channel=NotificationChannel.KIOSK,
            kind=NotificationKind.REQUEST_DENIED,
            details={
                "reference": "VR-ABC234",
                "days": [["2027-01-11", "full"], ["2027-01-12", "full"]],
                "comment": "<b>Inventory</b> week",
            },
        )
    )
    kiosk.db.flush()

    page = kiosk.client.get("/chat")

    assert "VR-ABC234 (2027-01-11 to 2027-01-12) was not approved" in page.text
    assert (
        "Comment: &lt;b&gt;Inventory&lt;/b&gt; week" in page.text
    )  # the approver's words, escaped
    assert "VR-ABC234" not in kiosk.client.get("/chat").text  # shown once
