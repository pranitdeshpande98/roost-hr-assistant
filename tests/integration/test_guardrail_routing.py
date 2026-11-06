"""The guardrail's HR contact lookup against real seeded employees."""

import uuid
from contextlib import nullcontext
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.core.enums import LocationKind
from roost.db.models import Employee, Location
from roost.guardrails.routing import HRContact, database_hr_contact
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(db_session: Session) -> Session:
    org = generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )
    load_seed_data(db_session, org, holiday_years=(2026,))
    return db_session


def _employee_at(session: Session, kind: LocationKind) -> uuid.UUID:
    return session.scalars(
        select(Employee.id).join(Location).where(Location.kind == kind).limit(1)
    ).one()


def test_each_employee_gets_the_hr_team_at_their_own_location(seeded: Session) -> None:
    lookup = database_hr_contact(lambda: nullcontext(seeded))
    plant, corporate = (
        _employee_at(seeded, k) for k in (LocationKind.PLANT, LocationKind.CORPORATE)
    )

    plant_contact = lookup(plant)
    assert plant_contact is not None
    assert plant_contact.location.endswith("Plant")
    assert plant_contact.phone.startswith("(555) 010-")
    assert lookup(corporate) == HRContact("Corporate Office", "(555) 010-4400")


def test_no_contact_before_sign_in_or_for_an_unknown_employee(seeded: Session) -> None:
    lookup = database_hr_contact(lambda: nullcontext(seeded))

    assert lookup(None) is None
    assert lookup(uuid.uuid4()) is None
