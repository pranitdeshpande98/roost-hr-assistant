"""Integration tests for roost.seed.loader."""

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roost.db.models import Employee, Holiday, LeaveBalance
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import is_seeded, load_seed_data, reset_hr_tables

pytestmark = pytest.mark.integration

AS_OF = date(2026, 11, 1)


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(as_of=AS_OF, seed=5, plant_headcount=40, corporate_headcount=30)


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_empty_database_is_not_seeded(db_session: Session) -> None:
    assert is_seeded(db_session) is False


def test_load_writes_every_row(db_session: Session, small_org: SeedData) -> None:
    counts = load_seed_data(db_session, small_org, holiday_years=(2026, 2027))

    assert is_seeded(db_session) is True
    assert counts["employee"] == _count(db_session, Employee) == len(small_org.employees)
    assert (
        counts["leave_balance"] == _count(db_session, LeaveBalance) == 2 * len(small_org.employees)
    )
    assert counts["holiday"] == _count(db_session, Holiday) == 12


def test_loaded_holidays_use_observed_dates(db_session: Session, small_org: SeedData) -> None:
    load_seed_data(db_session, small_org, holiday_years=(2026,))

    july = db_session.get(Holiday, date(2026, 7, 3))

    assert july is not None
    assert july.name == "Independence Day"
    assert db_session.get(Holiday, date(2026, 7, 4)) is None


def test_reset_empties_hr_tables(db_session: Session, small_org: SeedData) -> None:
    load_seed_data(db_session, small_org, holiday_years=(2026,))

    reset_hr_tables(db_session)

    assert is_seeded(db_session) is False
    assert _count(db_session, Holiday) == 0
