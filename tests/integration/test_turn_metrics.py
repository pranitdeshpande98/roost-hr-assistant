"""A kiosk turn leaves one row of numbers and a trace (NFR-08, NFR-11), against Postgres."""

import re
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolRegistry
from roost.api.app import create_app
from roost.api.services import Services
from roost.auth.kiosk import set_pin
from roost.auth.rate_limit import AttemptLimiter
from roost.core.enums import LocationKind, Role
from roost.db.models import Employee, EmployeeRole, Location, TurnMetric
from roost.llm.fake import ScriptedChatModel
from roost.llm.types import ChatMessage, ChatResult, Usage
from roost.observability.metrics import database_metrics, delete_old_metrics
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

PIN, OWN = "480715", "902614"


class Clock:
    now = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _answer(text: str) -> ChatResult:
    return ChatResult(
        message=ChatMessage(role="assistant", content=text),
        model="groq/openai/gpt-oss-120b",
        finish_reason="stop",
        usage=Usage(prompt_tokens=2000, completion_tokens=100),
        latency_ms=3.0,
    )


@pytest.fixture
def kiosk(db_session: Session) -> Any:
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
        select(Employee).where(Employee.location_id == hr.location_id, Employee.id != hr.id)
    ).first()
    assert worker is not None
    set_pin(db_session, worker.employee_number, PIN, by=hr.id)
    agent = build_graph(
        ScriptedChatModel([_answer("Fourteen days.")]),
        ToolRegistry([]),
        system_prompt=lambda state, today: "sys",
        messages=default_messages(),
        checkpointer=InMemorySaver(),
    )
    sessions = lambda: nullcontext(db_session)  # noqa: E731
    services = Services(
        sessions=sessions,
        agent=agent,
        limiter=AttemptLimiter(),
        clock=Clock(),
        record_turn=database_metrics(sessions),
    )
    client = TestClient(create_app(services), base_url="https://testserver")
    with client:
        client.post("/sign-in", data={"employee_number": worker.employee_number, "pin": PIN})
        page = client.get("/change-pin").text
        token = re.search(r'name="csrf" value="([^"]+)"', page)
        assert token is not None
        client.post(
            "/change-pin",
            data={"current_pin": PIN, "new_pin": OWN, "repeat_pin": OWN, "csrf": token.group(1)},
        )
        yield client, db_session


def _csrf(client: TestClient) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', client.get("/chat").text)
    assert match is not None
    return match.group(1)


def test_a_kiosk_turn_saves_its_numbers_and_nothing_it_said(kiosk: Any, spans: Any) -> None:
    client, db = kiosk

    client.post("/chat/messages", data={"text": "How much notice?", "csrf": _csrf(client)})

    row = db.scalars(select(TurnMetric)).one()
    assert (row.channel, row.language, row.reply_kind) == ("kiosk", "en", "answer")
    assert (row.input_tokens, row.output_tokens) == (2000, 100)
    assert row.cost_usd == Decimal("0.000360")  # 2000 * 0.15 + 100 * 0.60, per million
    assert row.model == "groq/openai/gpt-oss-120b"
    assert row.latency_ms >= 0
    values = {c.name: getattr(row, c.name) for c in TurnMetric.__table__.columns}
    assert "notice" not in str(values).lower()
    # The HTTP request span is the parent of the turn: one trace from browser to model.
    turn = spans.one("roost.turn")
    request = next(s for s in spans.all() if s.name == "POST /chat/messages")
    assert turn.context.trace_id == request.context.trace_id


def test_metrics_older_than_30_days_are_deleted(kiosk: Any) -> None:
    client, db = kiosk
    client.post("/chat/messages", data={"text": "Hi", "csrf": _csrf(client)})
    db.execute(update(TurnMetric).values(occurred_at=datetime(2026, 9, 1, tzinfo=UTC)))

    assert delete_old_metrics(db, now=datetime(2026, 10, 1, 1, 0, tzinfo=UTC)) == 1
    assert db.scalars(select(TurnMetric)).all() == []
    assert delete_old_metrics(db, now=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=1)) == 0
