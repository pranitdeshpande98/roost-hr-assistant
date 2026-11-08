"""Ratings (FR-47) and the Quality page (FR-51), against real Postgres."""

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.core.enums import Channel, Language
from roost.db.models import TranscriptMessage, TurnMetric
from roost.llm.fake import text_reply
from roost.observability.quality import quality_report
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data
from roost.transcripts.service import conversation_for, record_turn

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)

# The kiosk and console helper from the transcript tests.
from tests.integration.test_transcripts import PASSWORD, World, _csrf  # noqa: E402


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )


@pytest.fixture
def make_world(db_session: Session, small_org: SeedData) -> Iterator[Any]:
    load_seed_data(db_session, small_org, holiday_years=(2026,))
    worlds: list[World] = []

    def make(replies: list[Any] | None = None) -> World:
        world = World(db_session, replies or [])
        worlds.append(world)
        return world

    yield make
    for world in worlds:
        world.close()


def _metric(db: Session, **values: Any) -> None:
    row = {
        "channel": "kiosk",
        "language": "en",
        "resumed": False,
        "reply_kind": "answer",
        "latency_ms": 1000,
        "occurred_at": NOW - timedelta(hours=1),
    } | values
    db.add(TurnMetric(**row))
    db.flush()


# --- ratings -------------------------------------------------------------------------------


def test_an_answer_can_be_rated_and_the_rating_changed(make_world: Any) -> None:
    world: World = make_world([text_reply("Fourteen days.")])
    client = world.kiosk()
    page = world.say(client, "How much notice?").text
    message_id = re.search(r'name="message_id" value="(\d+)"', page)
    assert message_id is not None

    rated = client.post(
        "/chat/rating",
        data={"message_id": message_id.group(1), "value": "up", "csrf": _csrf(client, "/chat")},
        headers={"HX-Request": "true"},
    )

    assert rated.status_code == 200
    assert 'value="up" class="secondary"\n              aria-pressed="true"' in rated.text
    answer = world.db.get_one(TranscriptMessage, int(message_id.group(1)))
    assert answer.rating == 1
    client.post(
        "/chat/rating",
        data={"message_id": message_id.group(1), "value": "down", "csrf": _csrf(client, "/chat")},
    )
    world.db.refresh(answer)
    assert answer.rating == -1


def test_only_ones_own_answers_can_be_rated(make_world: Any) -> None:
    world: World = make_world([text_reply("Fourteen days.")])
    client = world.kiosk()
    world.say(client, "How much notice?")
    mine = world.conversation().messages
    question_id = mine[0].id  # the employee's own message: not an answer
    other = conversation_for(
        world.db,
        thread_id=uuid.uuid4(),
        employee_id=world.hr.id,
        channel=Channel.KIOSK,
        language=Language.EN,
        now=NOW,
    )
    record_turn(
        world.db,
        other,
        said="hi",
        reply="Hello.",
        kind="answer",
        category=None,
        resumed=False,
        now=NOW,
    )
    someone_elses = world.db.scalars(
        select(TranscriptMessage.id).where(
            TranscriptMessage.conversation_id == other.id, TranscriptMessage.role == "roost"
        )
    ).one()
    token = _csrf(client, "/chat")

    for target in (question_id, someone_elses):
        response = client.post(
            "/chat/rating", data={"message_id": target, "value": "up", "csrf": token}
        )
        assert response.status_code == 404
    forged = client.post(
        "/chat/rating", data={"message_id": mine[1].id, "value": "up", "csrf": "forged"}
    )
    assert forged.status_code == 403
    assert all(m.rating is None for m in world.db.scalars(select(TranscriptMessage)))


# --- the report ----------------------------------------------------------------------------


def test_the_report_counts_answers_unanswered_routed_and_cost(make_world: Any) -> None:
    world: World = make_world()
    db = world.db
    for latency in (800, 1200, 1500, 9000):
        _metric(
            db,
            latency_ms=latency,
            first_token_ms=latency // 2,
            model="groq/openai/gpt-oss-120b",
            input_tokens=1000,
            output_tokens=100,
            cost_usd=Decimal("0.00021"),
            category="none",
            guardrail_source="llm",
        )
    _metric(db, language="es", unanswered=True, searches=1, category="none", guardrail_source="llm")
    _metric(
        db, language="es", reply_kind="routed", category="harassment", guardrail_source="keyword"
    )
    _metric(db, reply_kind="fallback", model_failures=3)
    _metric(db, occurred_at=NOW - timedelta(days=10))  # outside a 7-day report

    report = quality_report(db, since=NOW - timedelta(days=7), now=NOW)

    assert report.turns == 7
    assert report.latency_p50_ms == 1000
    assert report.first_token_p95_ms is not None
    assert report.first_token_p95_ms > 3000  # 9000 / 2 pulls the slowest 1 in 20 over
    assert "warning_first_token" in report.warnings
    english, spanish = report.languages
    assert (english.language, english.answers, english.fallbacks) == ("en", 4, 1)
    assert (spanish.language, spanish.unanswered, spanish.routed) == ("es", 1, 1)
    assert spanish.unanswered_rate == 1.0
    assert report.cost_usd == Decimal("0.00084")
    assert report.models[0].turns == 4
    assert report.model_failures == 3
    assert {(g.category, g.source) for g in report.guardrail} == {
        ("none", "llm"),
        ("harassment", "keyword"),
    }
    assert "warning_similarity" not in report.warnings


def test_a_similarity_fallback_in_the_last_day_raises_the_warning(make_world: Any) -> None:
    world: World = make_world()
    _metric(world.db, category="harassment", guardrail_source="similarity")

    report = quality_report(world.db, since=NOW - timedelta(days=7), now=NOW)

    assert report.similarity_recent == 1
    assert report.warnings[0] == "warning_similarity"
    later = quality_report(world.db, since=NOW - timedelta(days=7), now=NOW + timedelta(days=2))
    assert "warning_similarity" not in later.warnings


def test_ratings_show_as_a_helpful_rate_by_language(make_world: Any) -> None:
    world: World = make_world()
    conversation = conversation_for(
        world.db,
        thread_id=uuid.uuid4(),
        employee_id=world.worker.id,
        channel=Channel.KIOSK,
        language=Language.VI,
        now=NOW,
    )
    for rating in (1, 1, 1, -1):
        record_turn(
            world.db,
            conversation,
            said="?",
            reply="Đây.",
            kind="answer",
            category=None,
            resumed=False,
            now=NOW,
        )
        newest = world.db.scalars(
            select(TranscriptMessage)
            .where(TranscriptMessage.conversation_id == conversation.id)
            .order_by(TranscriptMessage.id.desc())
            .limit(1)
        ).one()
        newest.rating = rating
    world.db.flush()

    [vietnamese] = quality_report(world.db, since=NOW - timedelta(days=7), now=NOW).languages

    assert (vietnamese.language, vietnamese.rated_up, vietnamese.rated_down) == ("vi", 3, 1)
    assert vietnamese.helpful_rate == 0.75


def test_only_hr_admins_see_the_quality_page(make_world: Any) -> None:
    world: World = make_world()
    world.clock.now = NOW
    _metric(world.db, category="injection", guardrail_source="similarity")
    hr, admin = world.console(world.hr), world.console(world.admin)

    assert hr.get("/console/quality").status_code == 404
    assert "/console/quality" not in hr.get("/console/queue").text
    page = admin.get("/console/quality?days=1")
    assert page.status_code == 200
    assert "backup check decided 1 message" in page.text
    assert "Backup check (AI classifier down)" in page.text
    assert admin.get("/console/quality?days=90").status_code == 422
    assert PASSWORD not in page.text
