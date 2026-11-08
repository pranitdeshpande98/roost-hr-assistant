"""Kiosk streaming over HTTP, against real Postgres (NFR-01, ADR-0014)."""

import json
import re
from contextlib import nullcontext
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.agent.confirmation import Confirmation
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.api.app import create_app
from roost.api.services import Services
from roost.auth.kiosk import set_pin
from roost.auth.rate_limit import AttemptLimiter
from roost.core.enums import LocationKind, Role
from roost.db.models import (
    Employee,
    EmployeeRole,
    Location,
    TranscriptMessage,
    TurnMetric,
    WebSession,
)
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall, ToolSpec
from roost.observability.metrics import database_metrics
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

PIN, OWN = "480715", "902614"
STREAM = {"HX-Request": "true", "X-Roost-Stream": "1"}


class Clock:
    def __call__(self) -> datetime:
        return datetime(2026, 11, 9, 14, 0, tzinfo=UTC)


class SubmitTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="submit", description="Submit.", parameters={})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        reply = context.confirm("Submit Monday? Reply YES or NO.")
        return "SUBMITTED." if reply.answer is Confirmation.YES else "NOT SUBMITTED."


def _events(response: Any) -> list[tuple[str, str]]:
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


class Kiosk:
    def __init__(self, db: Session, replies: list[Any]) -> None:
        self.db = db
        hr = db.scalars(
            select(Employee)
            .join(EmployeeRole)
            .join(Location, Location.id == Employee.location_id)
            .where(EmployeeRole.role == Role.HR_GENERALIST, Location.kind == LocationKind.PLANT)
            .limit(1)
        ).one()
        worker = db.scalars(
            select(Employee).where(Employee.location_id == hr.location_id, Employee.id != hr.id)
        ).first()
        assert worker is not None
        self.worker = worker
        set_pin(db, worker.employee_number, PIN, by=hr.id)
        self.model = ScriptedChatModel(replies)
        agent = build_graph(
            self.model,
            ToolRegistry([SubmitTool()]),
            system_prompt=lambda state, today: "sys",
            messages=default_messages(),
            checkpointer=InMemorySaver(),
        )
        sessions = lambda: nullcontext(db)  # noqa: E731
        services = Services(
            sessions=sessions,
            agent=agent,
            limiter=AttemptLimiter(),
            clock=Clock(),
            record_turn=database_metrics(sessions),
        )
        self.client = TestClient(create_app(services), base_url="https://testserver")
        self.client.__enter__()
        self.client.post("/sign-in", data={"employee_number": worker.employee_number, "pin": PIN})
        self.client.post(
            "/change-pin",
            data={
                "current_pin": PIN,
                "new_pin": OWN,
                "repeat_pin": OWN,
                "csrf": self.csrf("/change-pin"),
            },
        )

    def csrf(self, path: str = "/chat") -> str:
        match = re.search(r'name="csrf" value="([^"]+)"', self.client.get(path).text)
        assert match is not None
        return match.group(1)

    def send(self, text: str) -> Any:
        return self.client.post(
            "/chat/messages", data={"text": text, "csrf": self.csrf()}, headers=STREAM
        )

    def session(self) -> WebSession:
        return self.db.scalars(
            select(WebSession).where(WebSession.employee_id == self.worker.id)
        ).one()


@pytest.fixture
def make_kiosk(db_session: Session) -> Any:
    data = generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )
    load_seed_data(db_session, data, holiday_years=(2026,))
    made: list[Kiosk] = []

    def make(replies: list[Any]) -> Kiosk:
        kiosk = Kiosk(db_session, replies)
        made.append(kiosk)
        return kiosk

    yield make
    for kiosk in made:
        kiosk.client.__exit__(None, None, None)


def test_the_post_answers_at_once_and_the_stream_brings_the_reply(make_kiosk: Any) -> None:
    kiosk = make_kiosk([text_reply("You need **fourteen days** of notice [§9.9].")])

    posted = kiosk.send("How much notice?")

    assert posted.status_code == 200
    assert "How much notice?" in posted.text
    assert 'data-stream-url="/chat/stream"' in posted.text
    assert kiosk.model.requests == []  # nothing ran yet
    assert kiosk.session().pending_text == "How much notice?"

    stream = kiosk.client.get("/chat/stream")

    assert stream.headers["content-type"].startswith("text/event-stream")
    assert stream.headers["cache-control"] == "no-store"
    events = _events(stream)
    names = [name for name, _ in events]
    assert names[-1] == "done"
    streamed = "".join(data for name, data in events if name == "token")
    assert streamed == "You need **fourteen days** of notice [§9.9]."
    final = events[-1][1]
    # The final fragment is the checked text: markdown cleaned, the unreturned §9.9 removed.
    assert "You need fourteen days of notice." in final
    assert "[§9.9]" not in final
    assert "not an HR decision" in final
    assert kiosk.session().pending_text is None
    recorded = kiosk.db.scalars(select(TranscriptMessage.content)).all()
    assert recorded[0] == "How much notice?"
    assert "[§9.9]" not in recorded[1]
    metric = kiosk.db.scalars(select(TurnMetric)).one()
    assert metric.first_token_ms is not None


def test_a_message_is_streamed_once_and_a_reconnect_gets_nothing(make_kiosk: Any) -> None:
    kiosk = make_kiosk([text_reply("Hello.")])
    kiosk.send("Hi")
    kiosk.client.get("/chat/stream")

    again = kiosk.client.get("/chat/stream")

    assert again.status_code == 204  # tells EventSource not to reconnect
    assert len(kiosk.model.requests) == 1


def test_a_confirmation_question_arrives_through_the_stream(make_kiosk: Any) -> None:
    kiosk = make_kiosk(
        [tool_reply(ToolCall(id="c1", name="submit", arguments={})), text_reply("Sent.")]
    )
    kiosk.send("Submit Monday")
    final = _events(kiosk.client.get("/chat/stream"))[-1][1]
    assert "Submit Monday? Reply YES or NO." in final
    assert 'name="text" value="YES"' in final  # the buttons come with it

    kiosk.send("YES")
    final = _events(kiosk.client.get("/chat/stream"))[-1][1]

    assert "Submit Monday? Reply YES or NO." in final  # still on screen
    assert "Sent." in final


def test_no_stream_without_a_session(make_kiosk: Any) -> None:
    kiosk = make_kiosk([])
    kiosk.client.cookies.clear()

    assert kiosk.client.get("/chat/stream").status_code == 204


def test_without_the_stream_header_the_post_still_returns_the_reply(make_kiosk: Any) -> None:
    kiosk = make_kiosk([text_reply("Hello there.")])

    posted = kiosk.client.post(
        "/chat/messages",
        data={"text": "Hi", "csrf": kiosk.csrf()},
        headers={"HX-Request": "true"},
    )

    assert "Hello there." in posted.text
    assert "data-stream-url" not in posted.text
