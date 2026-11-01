"""Vacation tools against real Postgres, with a scripted employee answering confirmations."""

import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import date, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from roost.agent.confirmation import ConfirmationReply
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext
from roost.core.enums import Language, LeaveType, VacationRequestStatus
from roost.db.models import Department, Employee, LeaveBalance, Location, VacationRequest
from roost.seed.employees import SeedData, generate_seed_data
from roost.seed.loader import load_seed_data
from roost.tools.vacation import (
    NOT_SIGNED_IN,
    CancelVacationTool,
    ChangeVacationTool,
    ListVacationTool,
    RequestVacationTool,
)

pytestmark = pytest.mark.integration

TODAY = date(2026, 11, 2)
WEEK = [date(2027, 1, 11) + timedelta(days=i) for i in range(5)]
MESSAGES = default_messages()


class ScriptedEmployee:
    """Stands in for the person at the kiosk: answers each confirmation with the next scripted reply and records the questions."""

    def __init__(self, *replies: str) -> None:
        self._replies: Iterator[str] = iter(replies)
        self.questions: list[str] = []

    def __call__(self, question: str) -> ConfirmationReply:
        self.questions.append(question)
        return ConfirmationReply.from_text(next(self._replies))


@pytest.fixture(scope="module")
def small_org() -> SeedData:
    return generate_seed_data(
        as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
    )


@pytest.fixture
def alice(db_session: Session, small_org: SeedData) -> tuple[Session, uuid.UUID]:
    load_seed_data(db_session, small_org, holiday_years=(2026, 2027))
    employee_id = db_session.scalars(
        select(Employee.id)
        .join(Department)
        .join(Location, Location.id == Employee.location_id)
        .where(Department.name == "Finance", Location.code == "TX_CORP")
        .order_by(Employee.employee_number)
        .limit(1)
    ).one()
    db_session.execute(
        update(Employee).where(Employee.id == employee_id).values(hire_date=date(2020, 1, 6))
    )
    db_session.execute(
        update(LeaveBalance)
        .where(
            LeaveBalance.employee_id == employee_id, LeaveBalance.leave_type == LeaveType.VACATION
        )
        .values(accrued_hours=80, carried_over_hours=0, used_hours=0)
    )
    return db_session, employee_id


def _context(
    employee_id: uuid.UUID | None, employee: ScriptedEmployee, language: Language = Language.EN
) -> ToolContext:
    return ToolContext(employee_id=employee_id, language=language, today=TODAY, confirm=employee)


def _days(*days: date, portion: str = "full") -> dict[str, object]:
    return {"days": [{"date": d.isoformat(), "portion": portion} for d in days]}


def _requests(session: Session, employee_id: uuid.UUID) -> list[VacationRequest]:
    return list(
        session.scalars(select(VacationRequest).where(VacationRequest.employee_id == employee_id))
    )


def test_yes_submits_and_returns_a_reference(alice: tuple[Session, uuid.UUID]) -> None:
    session, employee_id = alice
    employee = ScriptedEmployee("yes")
    tool = RequestVacationTool(lambda: nullcontext(session), MESSAGES)

    result = tool.run(_days(*WEEK), _context(employee_id, employee))

    assert result.startswith("SUBMITTED. Reference VR-")
    assert len(_requests(session, employee_id)) == 1
    assert "2027-01-11 (full day)" in employee.questions[0]


def test_no_saves_nothing(alice: tuple[Session, uuid.UUID]) -> None:
    session, employee_id = alice
    tool = RequestVacationTool(lambda: nullcontext(session), MESSAGES)

    result = tool.run(_days(*WEEK), _context(employee_id, ScriptedEmployee("no")))

    assert result == "NOT SUBMITTED: the employee said no."
    assert _requests(session, employee_id) == []


def test_rule_failures_are_explained_without_asking(alice: tuple[Session, uuid.UUID]) -> None:
    session, employee_id = alice
    employee = ScriptedEmployee()  # would fail if asked: there is nothing to confirm
    tool = RequestVacationTool(lambda: nullcontext(session), MESSAGES)
    saturday = date(2027, 1, 16)

    result = tool.run(_days(TODAY + timedelta(days=3), saturday), _context(employee_id, employee))

    assert result.startswith("NOT SUBMITTED. The request breaks these rules.")
    assert "[§6.4]" in result  # notice
    assert "[§6.3]" in result  # Saturday is off shift...
    assert "2027-01-16" in result  # ...and the date is named
    assert employee.questions == []


def test_not_enough_vacation_never_reveals_the_balance(alice: tuple[Session, uuid.UUID]) -> None:
    session, employee_id = alice
    session.execute(update(LeaveBalance).values(accrued_hours=8))
    tool = RequestVacationTool(lambda: nullcontext(session), MESSAGES)

    result = tool.run(_days(*WEEK), _context(employee_id, ScriptedEmployee()))

    assert "[§6.8]" in result
    assert "8" not in result.replace("6.8", "")  # no hours or balance in what the model sees


def test_confirmation_question_is_in_the_employees_language(
    alice: tuple[Session, uuid.UUID],
) -> None:
    session, employee_id = alice
    employee = ScriptedEmployee("sí")
    tool = RequestVacationTool(lambda: nullcontext(session), MESSAGES)

    result = tool.run(_days(WEEK[0], portion="half"), _context(employee_id, employee, Language.ES))

    assert result.startswith("SUBMITTED.")
    assert "2027-01-11 (medio día)" in employee.questions[0]
    assert "Responda SÍ" in employee.questions[0]


def test_signed_out_employee_is_asked_to_sign_in(alice: tuple[Session, uuid.UUID]) -> None:
    session, _ = alice
    tool = RequestVacationTool(lambda: nullcontext(session), MESSAGES)

    assert tool.run(_days(*WEEK), _context(None, ScriptedEmployee())) == NOT_SIGNED_IN


def test_invalid_arguments_name_the_bad_field(alice: tuple[Session, uuid.UUID]) -> None:
    session, employee_id = alice
    tool = RequestVacationTool(lambda: nullcontext(session), MESSAGES)

    result = tool.run(
        {"days": [{"date": "next friday"}]}, _context(employee_id, ScriptedEmployee())
    )

    assert result.startswith("Error: invalid arguments (days.0.date)")


def test_change_then_cancel(alice: tuple[Session, uuid.UUID]) -> None:
    session, employee_id = alice

    def sessions() -> nullcontext[Session]:
        return nullcontext(session)

    RequestVacationTool(sessions, MESSAGES).run(
        _days(*WEEK), _context(employee_id, ScriptedEmployee("yes"))
    )
    code = _requests(session, employee_id)[0].reference_code

    changed = ChangeVacationTool(sessions, MESSAGES).run(
        {"reference_code": code.lower(), **_days(*WEEK[:2])},
        _context(employee_id, ScriptedEmployee("yes")),
    )
    listed = ListVacationTool(sessions, MESSAGES).run({}, _context(employee_id, ScriptedEmployee()))
    cancelled = CancelVacationTool(sessions, MESSAGES).run(
        {"reference_code": code}, _context(employee_id, ScriptedEmployee("yes"))
    )

    assert changed.startswith(f"CHANGED. {code}")
    assert listed == f"{code}: pending, 2027-01-11 to 2027-01-12 (2 days)"
    assert cancelled.startswith(f"CANCELLED. {code}")
    assert _requests(session, employee_id)[0].status is VacationRequestStatus.CANCELLED


def test_unknown_reference_is_reported(alice: tuple[Session, uuid.UUID]) -> None:
    session, employee_id = alice
    tool = CancelVacationTool(lambda: nullcontext(session), MESSAGES)

    result = tool.run({"reference_code": "VR-ZZZZZZ"}, _context(employee_id, ScriptedEmployee()))

    assert result == "Error: no request VR-ZZZZZZ for this employee."
