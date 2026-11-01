"""Tests for roost.evals.agent_dataset and the real evals/datasets/agent_v1.yaml."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from roost.agent.messages import default_messages
from roost.core.enums import Language
from roost.evals.agent_dataset import (
    EVAL_TODAY,
    KNOWN_TOOLS,
    Scenario,
    expand_cases,
    load_agent_scenarios,
    mentions,
)
from roost.tools.handbook import HandbookSearchTool
from roost.tools.payroll import PayScheduleTool
from roost.tools.vacation import vacation_tools

DATASET = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "agent_v1.yaml"
SAY = {"en": "hi", "es": "hola", "vi": "chào"}


def _scenario(**overrides: Any) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "id": "x",
        "category": "handbook",
        "employee": "office_biweekly",
        "turns": [{"say": SAY}],
    }
    scenario.update(overrides)
    return scenario


# --- the real dataset ---------------------------------------------------------------


def test_real_dataset_loads_and_covers_every_category() -> None:
    scenarios = load_agent_scenarios(DATASET)

    assert len(scenarios) == 26
    assert {s.category for s in scenarios} == {
        "handbook",
        "payday",
        "vacation",
        "restricted",
        "out_of_scope",
        "multi_turn",
    }


def test_every_scenario_runs_in_every_language() -> None:
    cases = expand_cases(load_agent_scenarios(DATASET))

    assert len(cases) == 26 * 3
    assert len({c.case_id for c in cases}) == len(cases)
    assert {c.language for c in cases} == set(Language)


def test_known_tools_match_what_the_agent_really_offers() -> None:
    # If a tool is renamed or added, the dataset must be updated with it.
    def unused() -> Any:
        raise AssertionError("not called")

    tools = [
        HandbookSearchTool(lambda query: []),
        *vacation_tools(unused, default_messages()),
        PayScheduleTool(unused),
    ]

    assert {tool.spec.name for tool in tools} == KNOWN_TOOLS


def test_eval_today_is_the_monday_the_questions_assume() -> None:
    assert EVAL_TODAY.isoformat() == "2026-11-02"
    assert EVAL_TODAY.strftime("%A") == "Monday"  # "next Monday" is 2026-11-09


def test_vacation_dates_in_the_dataset_are_after_eval_today() -> None:
    for scenario in load_agent_scenarios(DATASET):
        for turn in scenario.turns:
            for args in turn.expect.args.values():
                assert all(day.date > EVAL_TODAY for day in args.days), scenario.id


# --- validation ---------------------------------------------------------------------


def test_a_missing_language_is_rejected() -> None:
    with pytest.raises(ValidationError, match="vi"):
        Scenario.model_validate(_scenario(turns=[{"say": {"en": "hi", "es": "hola"}}]))


def test_an_unknown_tool_is_rejected() -> None:
    turns = [{"say": SAY, "expect": {"tools": ["serch_handbook"]}}]

    with pytest.raises(ValidationError, match="unknown tools: serch_handbook"):
        Scenario.model_validate(_scenario(turns=turns))


def test_a_misspelled_expectation_key_is_rejected() -> None:
    turns = [{"say": SAY, "expect": {"mention": ["10"]}}]

    with pytest.raises(ValidationError, match="mention"):
        Scenario.model_validate(_scenario(turns=turns))


def test_two_paused_turns_in_a_row_are_rejected() -> None:
    paused = {"say": SAY, "expect": {"pauses": True}}

    with pytest.raises(ValidationError, match="two paused turns"):
        Scenario.model_validate(_scenario(turns=[paused, paused]))


def test_duplicate_scenario_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump([_scenario(), _scenario()]), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate scenario ids: x"):
        load_agent_scenarios(path)


def test_case_messages_are_in_the_case_language() -> None:
    case = expand_cases([Scenario.model_validate(_scenario())])[2]

    assert case.case_id == "x-vi"
    assert case.messages == ["chào"]


# --- mentions -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected", "found"),
    [
        ("Your next payday is November 6.", "6", True),
        ("Próximo pago: 2026-11-06.", "6", True),  # "06" is the number 6
        ("Paid in 2026.", "6", False),  # "2026" is not 6
        ("You get bereavement leave [§9.2].", "2", False),  # citations do not count
        ("You get 2 days [§9.2].", "2", True),
        ("Reference VR-7K2M9Q.", "VR-", True),
        ("reference vr-7k2m9q", "VR-", True),  # substrings ignore case
        ("No reference yet.", "VR-", False),
    ],
)
def test_mentions(reply: str, expected: str, found: bool) -> None:
    assert mentions(reply, expected) is found
