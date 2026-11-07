"""Outbox delivery and wording against real Postgres (FR-22)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roost.core.enums import Language, NotificationChannel, NotificationKind
from roost.db.models import Employee, Notification
from roost.notifications.delivery import deliver_to_folder
from roost.notifications.render import default_notification_text
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def staff(db_session: Session) -> tuple[Session, Employee]:
    data = generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )
    load_seed_data(db_session, data, holiday_years=(2026,))
    person = db_session.scalars(select(Employee).where(Employee.email.is_not(None))).first()
    assert person is not None
    db_session.execute(
        update(Employee)
        .where(Employee.id == person.id)
        .values(preferred_language=Language.ES, has_teams=False)
    )
    return db_session, person


def _add(session: Session, recipient: Employee, channel: NotificationChannel) -> Notification:
    notification = Notification(
        recipient_id=recipient.id,
        channel=channel,
        kind=NotificationKind.REQUEST_APPROVED,
        details={"reference": "VR-XYZ789", "days": [["2027-01-11", "full"]]},
    )
    session.add(notification)
    session.flush()
    return notification


def test_email_is_written_in_the_recipients_language_and_marked_sent(
    staff: tuple[Session, Employee], tmp_path: Path
) -> None:
    session, person = staff
    email = _add(session, person, NotificationChannel.EMAIL)
    kiosk = _add(session, person, NotificationChannel.KIOSK)

    assert deliver_to_folder(session, tmp_path, now=NOW) == 1

    [written] = list(tmp_path.iterdir())
    message = json.loads(written.read_text(encoding="utf-8"))
    assert message == {
        "channel": "email",
        "to": person.email,
        "subject": "Solicitud de vacaciones VR-XYZ789 aprobada",
        "body": "Su solicitud de vacaciones VR-XYZ789 (2027-01-11) fue aprobada.",
    }
    assert email.sent_at == NOW
    assert kiosk.sent_at is None  # the kiosk shows it at sign-in
    assert deliver_to_folder(session, tmp_path, now=NOW) == 0


def test_every_notification_kind_has_a_subject_and_a_body() -> None:
    keys = default_notification_text().keys()
    for kind in NotificationKind:
        assert {f"subject_{kind.value}", f"body_{kind.value}"} <= keys
