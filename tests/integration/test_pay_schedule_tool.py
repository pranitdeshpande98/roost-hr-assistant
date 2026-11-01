"""my_pay_schedule against a real seeded employee."""

import uuid
from contextlib import nullcontext
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.agent.confirmation import ConfirmationReply
from roost.agent.tools import ToolContext
from roost.core.enums import Language, PayrollClass
from roost.db.models import Employee
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data
from roost.tools.payroll import PayScheduleTool

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(db_session: Session) -> Session:
    org = generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )
    load_seed_data(db_session, org, holiday_years=(2026,))
    return db_session


def _employee(session: Session, payroll_class: PayrollClass) -> uuid.UUID:
    return session.scalars(
        select(Employee.id).where(Employee.payroll_class == payroll_class).limit(1)
    ).one()


def _context(employee_id: uuid.UUID | None) -> ToolContext:
    return ToolContext(
        employee_id=employee_id,
        language=Language.EN,
        today=date(2026, 11, 1),
        confirm=ConfirmationReply.from_text,
    )


@pytest.mark.parametrize(
    ("payroll_class", "first_payday"),
    [
        (PayrollClass.WEEKLY_PRODUCTION, "2026-11-06"),
        (PayrollClass.DRIVER, "2026-11-06"),
        (PayrollClass.BIWEEKLY, "2026-11-13"),
    ],
)
def test_uses_the_employees_own_payroll_class(
    seeded: Session, payroll_class: PayrollClass, first_payday: str
) -> None:
    tool = PayScheduleTool(lambda: nullcontext(seeded))

    result = tool.run({}, _context(_employee(seeded, payroll_class)))

    assert f"Next payday: {first_payday}" in result


def test_unknown_employee_is_reported(seeded: Session) -> None:
    tool = PayScheduleTool(lambda: nullcontext(seeded))

    assert tool.run({}, _context(uuid.uuid4())) == "Error: employee not found."
