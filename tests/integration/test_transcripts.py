"""Transcripts, legal holds and retention, against real Postgres (FR-45, FR-46, FR-48)."""

import re
import uuid
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from roost.agent.confirmation import Confirmation
from roost.agent.conversation import ask
from roost.agent.graph import build_graph
from roost.agent.memory import open_checkpointer
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.api.app import create_app
from roost.api.services import Services
from roost.auth.console import set_password
from roost.auth.kiosk import set_pin
from roost.auth.rate_limit import AttemptLimiter
from roost.core.enums import AuditAction, Channel, Language, Role
from roost.db.models import (
    AuditLog,
    Conversation,
    Department,
    Employee,
    EmployeeRole,
    Location,
    TranscriptMessage,
    WebSession,
)
from roost.guardrails.classifier import GuardrailDecision, Source
from roost.guardrails.types import Category
from roost.leave.approvals import hr_team
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall, ToolSpec
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data
from roost.transcripts.retention import PostgresMemory, apply_retention
from roost.transcripts.service import (
    conversation_for,
    list_conversations,
    place_hold,
    record_turn,
)

pytestmark = pytest.mark.integration

PIN, OWN_PIN = "480715", "902614"
PASSWORD, OWN_PASSWORD = "temporary-pass-4471", "maple tractor cloud river"
MESSAGES = default_messages()
SECRET = "My lead grabbed my arm by the freezer"
NOW = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class SubmitTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="submit", description="Submit.", parameters={})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        reply = context.confirm("Submit Monday? Reply YES or NO.")
        return "SUBMITTED." if reply.answer is Confirmation.YES else "NOT SUBMITTED."


class FakeScreen:
    def classify(self, text: str) -> GuardrailDecision:
        category = Category.HARASSMENT if text == SECRET else Category.NONE
        return GuardrailDecision(category, Source.LLM, "fake")


def _csrf(client: TestClient, path: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert match is not None, path
    return match.group(1)


class World:
    def __init__(self, db: Session, replies: list[Any]) -> None:
        self.db = db
        people = db.scalars(
            select(Employee)
            .join(Department)
            .join(Location, Location.id == Employee.location_id)
            .where(Department.name == "Finance", Location.code == "TX_CORP")
            .order_by(Employee.employee_number)
        ).all()
        self.worker = people[0]
        self.hr = hr_team(db, self.worker.location_id)[0]
        admins = db.scalars(
            select(Employee)
            .join(EmployeeRole)
            .where(EmployeeRole.role == Role.HR_ADMIN)
            .order_by(Employee.employee_number)
        ).all()
        self.admin, self.other_admin = admins[0], admins[1]
        set_pin(db, self.worker.employee_number, PIN, by=self.hr.id)
        set_password(db, self.admin.employee_number, PASSWORD, by=self.other_admin.id)
        set_password(db, self.hr.employee_number, PASSWORD, by=self.admin.id)
        self.clock = Clock()
        agent = build_graph(
            ScriptedChatModel(replies),
            ToolRegistry([SubmitTool()]),
            system_prompt=lambda state, today: "sys",
            messages=MESSAGES,
            checkpointer=InMemorySaver(),
            screen=FakeScreen(),
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
        client = TestClient(self.app, base_url="https://testserver")
        client.__enter__()
        self.clients.append(client)
        return client

    def kiosk(self) -> TestClient:
        client = self.client()
        client.post("/sign-in", data={"employee_number": self.worker.employee_number, "pin": PIN})
        client.post(
            "/change-pin",
            data={
                "current_pin": PIN,
                "new_pin": OWN_PIN,
                "repeat_pin": OWN_PIN,
                "csrf": _csrf(client, "/change-pin"),
            },
        )
        return client

    def console(self, person: Employee) -> TestClient:
        client = self.client()
        client.post(
            "/console/sign-in",
            data={"employee_number": person.employee_number, "password": PASSWORD},
        )
        client.post(
            "/console/change-password",
            data={
                "current_password": PASSWORD,
                "new_password": OWN_PASSWORD,
                "repeat_password": OWN_PASSWORD,
                "csrf": _csrf(client, "/console/change-password"),
            },
        )
        return client

    def say(self, client: TestClient, text: str) -> Any:
        return client.post(
            "/chat/messages",
            data={"text": text, "csrf": _csrf(client, "/chat")},
            headers={"HX-Request": "true"},
        )

    def conversation(self) -> Conversation:
        return self.db.scalars(
            select(Conversation).where(Conversation.employee_id == self.worker.id)
        ).one()

    def close(self) -> None:
        for client in self.clients:
            client.__exit__(None, None, None)


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )


@pytest.fixture
def make_world(db_session: Session, small_org: SeedData) -> Any:
    load_seed_data(db_session, small_org, holiday_years=(2026,))
    worlds: list[World] = []

    def make(replies: list[Any] | None = None) -> World:
        world = World(db_session, replies or [])
        worlds.append(world)
        return world

    yield make
    for world in worlds:
        world.close()


# --- recording ----------------------------------------------------------------------------


def test_the_confirmation_question_stays_on_screen_and_in_the_record(make_world: Any) -> None:
    world = make_world(
        [tool_reply(ToolCall(id="c1", name="submit", arguments={})), text_reply("Sent.")]
    )
    client = world.kiosk()

    world.say(client, "Submit Monday off")
    fragment = world.say(client, "YES").text

    assert "Submit Monday? Reply YES or NO." in fragment  # still shown after answering
    assert "Sent." in fragment
    rows = [(m.role, m.kind, m.content.split("\n")[0]) for m in world.conversation().messages]
    assert rows == [
        ("employee", "message", "Submit Monday off"),
        ("roost", "question", "Submit Monday? Reply YES or NO."),
        ("employee", "answer", "YES"),
        ("roost", "answer", "Sent."),
    ]


def test_only_routed_turns_carry_a_category(make_world: Any) -> None:
    world = make_world([text_reply("Fourteen days.")])
    client = world.kiosk()

    world.say(client, "How much notice?")
    world.say(client, SECRET)

    categories = [(m.role, m.category) for m in world.conversation().messages]
    assert categories[:2] == [("employee", None), ("roost", None)]
    assert categories[2:] == [("employee", "harassment"), ("roost", "harassment")]


# --- HR admins only ------------------------------------------------------------------------


def test_only_hr_admins_can_open_transcripts_and_each_view_is_audited(make_world: Any) -> None:
    world = make_world([text_reply("Fourteen days.")])
    world.say(world.kiosk(), "How much notice?")
    conversation = world.conversation()
    hr, admin = world.console(world.hr), world.console(world.admin)

    assert "/console/transcripts" not in hr.get("/console/queue").text
    assert hr.get("/console/transcripts").status_code == 404
    assert hr.get(f"/console/transcripts/{conversation.id}").status_code == 404

    listing = admin.get("/console/transcripts").text
    assert world.worker.employee_number in listing
    page = admin.get(f"/console/transcripts/{conversation.id}").text
    assert "How much notice?" in page
    assert "Fourteen days." in page
    viewed = world.db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.TRANSCRIPT_VIEWED)
    ).all()
    assert [(v.actor_employee_id, v.entity_id) for v in viewed] == [
        (world.admin.id, str(conversation.id))
    ]
    assert viewed[0].details in ({}, None)  # who and which, never what


def test_an_hr_admin_never_sees_their_own_conversation(make_world: Any) -> None:
    world = make_world()
    own = conversation_for(
        world.db,
        thread_id=uuid.uuid4(),
        employee_id=world.admin.id,
        channel=Channel.KIOSK,
        language=Language.EN,
        now=NOW,
    )
    record_turn(
        world.db,
        own,
        said="hi",
        reply="Hello.",
        kind="answer",
        category=None,
        resumed=False,
        now=NOW,
    )

    assert list_conversations(world.db, world.admin.id) == []
    assert world.console(world.admin).get(f"/console/transcripts/{own.id}").status_code == 404


def test_a_legal_hold_is_placed_and_released_with_csrf_and_audit(make_world: Any) -> None:
    world = make_world([text_reply("Fourteen days.")])
    world.say(world.kiosk(), "How much notice?")
    conversation = world.conversation()
    admin = world.console(world.admin)
    page = f"/console/transcripts/{conversation.id}"

    forged = admin.post(f"{page}/hold", data={"reason": "Case 2026-114", "csrf": "forged"})
    assert forged.status_code == 403
    held = admin.post(
        f"{page}/hold",
        data={"reason": "Case 2026-114", "csrf": _csrf(admin, page)},
        follow_redirects=False,
    )
    assert held.headers["location"] == f"{page}?done=held"
    world.db.refresh(conversation)
    assert (conversation.legal_hold, conversation.held_by_id) == (True, world.admin.id)
    assert conversation.hold_reason == "Case 2026-114"
    assert "Case 2026-114" in admin.get(page).text

    admin.post(f"{page}/release", data={"csrf": _csrf(admin, page)})
    world.db.refresh(conversation)
    assert not conversation.legal_hold
    actions = world.db.scalars(
        select(AuditLog.action)
        .where(AuditLog.entity_id == str(conversation.id))
        .order_by(AuditLog.id)
    ).all()
    assert AuditAction.LEGAL_HOLD_PLACED in actions
    assert AuditAction.LEGAL_HOLD_RELEASED in actions


# --- retention -----------------------------------------------------------------------------


class FakeMemory:
    def __init__(self, threads: list[str]) -> None:
        self.kept = list(threads)
        self.forgotten: list[str] = []

    def threads(self) -> list[str]:
        return list(self.kept)

    def forget(self, thread_id: str) -> None:
        self.kept.remove(thread_id)
        self.forgotten.append(thread_id)


def _conversation(world: World, last: datetime, *, held: bool = False) -> Conversation:
    conversation = conversation_for(
        world.db,
        thread_id=uuid.uuid4(),
        employee_id=world.worker.id,
        channel=Channel.KIOSK,
        language=Language.ES,
        now=last,
    )
    record_turn(
        world.db,
        conversation,
        said="hola",
        reply="Hola.",
        kind="answer",
        category=None,
        resumed=False,
        now=last,
    )
    if held:
        assert place_hold(world.db, world.admin.id, conversation.id, reason="case", now=last)
    return conversation


def test_retention_deletes_after_45_days_unless_held_and_forgets_unused_memory(
    make_world: Any,
) -> None:
    world = make_world()
    old = _conversation(world, NOW - timedelta(days=46))
    held = _conversation(world, NOW - timedelta(days=200), held=True)
    recent = _conversation(world, NOW - timedelta(days=44))
    live_thread = str(uuid.uuid4())
    world.db.add(
        WebSession(
            token_hash="a" * 64,
            employee_id=world.worker.id,
            kiosk_id="k1",
            language=Language.EN,
            thread_id=uuid.UUID(live_thread),
            csrf_token="c",
            last_seen_at=NOW,
        )
    )
    orphan = str(uuid.uuid4())  # a sign-in that never wrote a message, long gone
    memory = FakeMemory(
        [str(old.thread_id), str(held.thread_id), str(recent.thread_id), live_thread, orphan]
    )
    old_id = old.id

    report = apply_retention(world.db, memory, now=NOW)

    assert report.conversations == 1
    remaining = set(world.db.scalars(select(Conversation.id)))
    assert old_id not in remaining
    assert {held.id, recent.id} <= remaining
    assert (
        world.db.scalars(
            select(TranscriptMessage).where(TranscriptMessage.conversation_id == old_id)
        ).all()
        == []
    )
    assert sorted(memory.forgotten) == sorted([str(old.thread_id), orphan])


def test_released_conversations_older_than_45_days_go_at_the_next_run(make_world: Any) -> None:
    world = make_world()
    held = _conversation(world, NOW - timedelta(days=100), held=True)
    world.db.execute(
        update(Conversation)
        .where(Conversation.id == held.id)
        .values(legal_hold=False, held_by_id=None, held_at=None, hold_reason=None)
    )

    assert apply_retention(world.db, FakeMemory([]), now=NOW).conversations == 1


def test_postgres_memory_lists_and_forgets_real_checkpoints(engine: Engine) -> None:
    url = engine.url.render_as_string(hide_password=False)
    thread = f"retention-{uuid.uuid4()}"
    with open_checkpointer(url) as saver:
        agent = build_graph(
            ScriptedChatModel([text_reply("Hi.")]),
            ToolRegistry([]),
            system_prompt=lambda state, today: "sys",
            messages=MESSAGES,
            checkpointer=saver,
        )
        ask(agent, thread, "hello", language=Language.EN, channel=Channel.KIOSK)
        memory = PostgresMemory(saver)

        assert thread in memory.threads()
        memory.forget(thread)
        assert thread not in memory.threads()
