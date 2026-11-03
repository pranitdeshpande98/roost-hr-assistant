"""The harness against real Postgres: employee profiles and the rollback guarantee."""

from datetime import date
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from roost.agent.factory import build_agent
from roost.core.enums import Language, PayrollClass
from roost.db.models import Employee, Shift, VacationRequest
from roost.evals.agent_dataset import EVAL_TODAY, AgentCase, EmployeeProfile, Scenario
from roost.evals.agent_harness import (
    RecordingChatModel,
    Sessions,
    pick_employee,
    rolled_back_sessions,
    run_case,
)
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall
from roost.seed.employees import generate_seed_data
from roost.seed.loader import load_seed_data, reset_hr_tables

pytestmark = pytest.mark.integration


class NoEmbedder:
    model_name = "none"

    def embed_documents(self, texts: Any) -> list[list[float]]:
        raise AssertionError("not used")

    def embed_queries(self, texts: Any) -> list[list[float]]:
        raise AssertionError("not used")


@pytest.fixture
def seeded_engine(engine: Engine) -> Any:
    """Seed committed data the harness's own connections can see; clean up afterwards."""
    with Session(engine) as session:
        org = generate_seed_data(
            as_of=date(2026, 11, 1), seed=3, plant_headcount=40, corporate_headcount=30
        )
        load_seed_data(session, org, holiday_years=(2026,))
        session.commit()
    yield engine
    with Session(engine) as session:
        reset_hr_tables(session)
        session.commit()


@pytest.mark.parametrize(
    ("profile", "payroll_class", "shift"),
    [
        (EmployeeProfile.OFFICE_BIWEEKLY, PayrollClass.BIWEEKLY, "OFFICE"),
        (EmployeeProfile.PRODUCTION_WEEKLY, PayrollClass.WEEKLY_PRODUCTION, "PROD_1ST"),
    ],
)
def test_pick_employee_matches_the_profile(
    seeded_engine: Engine, profile: EmployeeProfile, payroll_class: PayrollClass, shift: str
) -> None:
    with Session(seeded_engine) as session:
        employee_id = pick_employee(session, profile, EVAL_TODAY)
        employee = session.get_one(Employee, employee_id)
        shift_code = session.scalar(select(Shift.code).where(Shift.id == employee.shift_id))

    assert employee.payroll_class is payroll_class
    assert shift_code == shift
    assert (EVAL_TODAY - employee.hire_date).days > 365


def test_pick_employee_is_deterministic(seeded_engine: Engine) -> None:
    with Session(seeded_engine) as session:
        first = pick_employee(session, EmployeeProfile.OFFICE_BIWEEKLY, EVAL_TODAY)
        second = pick_employee(session, EmployeeProfile.OFFICE_BIWEEKLY, EVAL_TODAY)

    assert first == second


def test_a_booked_vacation_is_rolled_back_after_the_case(seeded_engine: Engine) -> None:
    scenario = Scenario.model_validate(
        {
            "id": "book",
            "category": "vacation",
            "employee": "office_biweekly",
            "turns": [
                {"say": {"en": "Dec 14 off", "es": "x", "vi": "x"}},
                {"say": {"en": "yes", "es": "sí", "vi": "có"}},
            ],
        }
    )
    case = AgentCase(case_id="book-en", scenario=scenario, language=Language.EN)
    days = {"days": [{"date": "2026-12-14", "portion": "full"}]}
    model = RecordingChatModel(
        ScriptedChatModel(
            [
                tool_reply(ToolCall(id="c1", name="request_vacation", arguments=days)),
                text_reply("Booked."),
            ]
        )
    )

    def make_agent(sessions: Sessions) -> Any:
        return build_agent(
            sessions,
            model=model,
            embedder=NoEmbedder(),
            checkpointer=InMemorySaver(),
            clock=lambda: EVAL_TODAY,
        )

    with rolled_back_sessions(seeded_engine) as sessions:
        with sessions() as session:
            employee = pick_employee(session, EmployeeProfile.OFFICE_BIWEEKLY, EVAL_TODAY)
        record = run_case(case, make_agent, model, sessions, employee)
        with sessions() as session:  # visible inside the case...
            inside = session.scalar(select(func.count()).select_from(VacationRequest))

    with Session(seeded_engine) as session:  # ...and gone afterwards
        after = session.scalar(select(func.count()).select_from(VacationRequest))

    assert record.turns[1].tool_calls[0].result.startswith("SUBMITTED. Reference VR-")
    assert inside == 1
    assert after == 0
