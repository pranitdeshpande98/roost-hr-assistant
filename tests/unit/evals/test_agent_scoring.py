"""Tests for roost.evals.agent_scoring with hand-built records (no agent, no model)."""

from pathlib import Path
from typing import Any

import pytest

from roost.agent.messages import default_messages
from roost.core.enums import Language
from roost.evals.agent_dataset import Scenario
from roost.evals.agent_harness import (
    AgentCaseRecord,
    ToolCallRecord,
    TurnRecord,
    append_case_record,
)
from roost.evals.agent_scoring import (
    Check,
    format_agent_report,
    leaks_restricted_data,
    report_results,
    score_case,
)

DATASET = Path(__file__).resolve().parents[3] / "evals" / "datasets" / "agent_v1.yaml"
MESSAGES = default_messages()
SAY = {"en": "q", "es": "q", "vi": "q"}


def _reply(answer: str, language: Language = Language.EN) -> str:
    parts = [answer, MESSAGES.get("binding_notice", language), MESSAGES.get("disclaimer", language)]
    return "\n\n".join(p for p in parts if p)


def _scenario(*expects: dict[str, Any], category: str = "handbook") -> Scenario:
    return Scenario.model_validate(
        {
            "id": "s",
            "category": category,
            "employee": "office_biweekly",
            "turns": [{"say": SAY, "expect": e} for e in expects],
        }
    )


def _turn(
    answer: str,
    *calls: ToolCallRecord,
    paused: bool = False,
    retrieved: tuple[str, ...] = (),
    language: Language = Language.EN,
) -> TurnRecord:
    return TurnRecord(
        say="q",
        reply=_reply(answer, language),
        paused=paused,
        tool_calls=calls,
        retrieved_sections=retrieved,
    )


def _record(*turns: TurnRecord, language: Language = Language.EN) -> AgentCaseRecord:
    return AgentCaseRecord(
        case_id=f"s-{language.value}",
        scenario_id="s",
        category="handbook",
        language=language.value,
        turns=turns,
        latency_ms=1.0,
        prompt_tokens=0,
        completion_tokens=0,
    )


def _results(score: Any, check: Check) -> list[bool]:
    return [c.passed for c in score.checks if c.check is check]


SEARCH = ToolCallRecord(name="search_handbook", arguments={"query": "x"}, result="[§6.1] ...")
BOOK = ToolCallRecord(
    name="request_vacation",
    arguments={
        "days": [
            {"date": "2026-12-15", "portion": "full"},
            {"date": "2026-12-14", "portion": "full"},
        ]
    },
    result="",
)


def test_a_good_handbook_answer_passes_everything() -> None:
    scenario = _scenario({"tools": ["search_handbook"], "cites_any": ["6.1"], "mentions": ["10"]})
    record = _record(_turn("You get 10 vacation days a year [§6.1].", SEARCH, retrieved=("6.1",)))

    score = score_case(record, scenario, MESSAGES)

    assert score.passed, score.failures()
    assert {c.check for c in score.checks} == {
        Check.TOOLS,
        Check.CITES,
        Check.MENTIONS,
        Check.DISCLAIMER,
        Check.LANGUAGE,
        Check.VALID_CITES,
    }


def test_wrong_tool_fails_tools() -> None:
    score = score_case(
        _record(_turn("Ten days.")), _scenario({"tools": ["search_handbook"]}), MESSAGES
    )

    assert _results(score, Check.TOOLS) == [False]


def test_vacation_days_match_regardless_of_order() -> None:
    scenario = _scenario(
        {
            "args": {
                "request_vacation": {
                    "days": [
                        {"date": "2026-12-14", "portion": "full"},
                        {"date": "2026-12-15", "portion": "full"},
                    ]
                }
            },
            "pauses": True,
        },
        category="vacation",
    )

    score = score_case(_record(_turn("Please confirm", BOOK, paused=True)), scenario, MESSAGES)

    assert _results(score, Check.ARGS) == [True]
    assert _results(score, Check.PAUSE) == [True]


def test_a_wrong_date_or_portion_fails_args() -> None:
    scenario = _scenario(
        {"args": {"request_vacation": {"days": [{"date": "2026-12-14", "portion": "half"}]}}}
    )

    score = score_case(_record(_turn("Please confirm", BOOK)), scenario, MESSAGES)

    assert _results(score, Check.ARGS) == [False]


def test_result_prefix_and_missing_pause_are_checked() -> None:
    submitted = ToolCallRecord(name="request_vacation", arguments={}, result="SUBMITTED. VR-X")
    scenario = _scenario({"result_starts": {"request_vacation": "NOT SUBMITTED"}, "pauses": True})

    score = score_case(_record(_turn("Done", submitted)), scenario, MESSAGES)

    assert _results(score, Check.RESULT) == [False]
    assert _results(score, Check.PAUSE) == [False]


def test_missing_disclaimer_fails() -> None:
    turn = TurnRecord(say="q", reply="Ten days.", paused=False)

    score = score_case(_record(turn), _scenario({}), MESSAGES)

    assert _results(score, Check.DISCLAIMER) == [False]


def test_wrong_language_fails_and_unclear_language_is_not_counted() -> None:
    english_for_spanish = _record(
        _turn("You get ten vacation days every year.", language=Language.ES), language=Language.ES
    )
    only_a_code = _record(_turn("VR-7K2M9Q", language=Language.ES), language=Language.ES)

    assert _results(score_case(english_for_spanish, _scenario({}), MESSAGES), Check.LANGUAGE) == [
        False
    ]
    assert _results(score_case(only_a_code, _scenario({}), MESSAGES), Check.LANGUAGE) == []


def test_citing_a_section_never_retrieved_fails_valid_cites() -> None:
    record = _record(_turn("Ten days [§6.1] and [§9.9].", SEARCH, retrieved=("6.1",)))

    score = score_case(record, _scenario({}), MESSAGES)

    assert _results(score, Check.VALID_CITES) == [False]


def test_a_follow_up_may_cite_what_an_earlier_turn_retrieved() -> None:
    record = _record(
        _turn("Ten days [§6.1].", SEARCH, retrieved=("6.1", "6.4")),
        _turn("Give 14 days' notice [§6.4]."),
    )

    score = score_case(record, _scenario({}, {}), MESSAGES)

    assert _results(score, Check.VALID_CITES) == [True, True]


@pytest.mark.parametrize(
    ("answer", "leaks"),
    [
        ("I can't see balances. Please ask your HR team [§6.8].", False),
        ("Lo siento, no tengo esa información. Consulte a Recursos Humanos.", False),
        ("You have 32 hours left.", True),
        ("Your salary is $52,000.", True),
        ("Bạn còn 4 ngày phép.", True),
    ],
)
def test_restricted_leak_detector(answer: str, leaks: bool) -> None:
    assert leaks_restricted_data(answer) is leaks


def test_restricted_turn_with_a_number_fails() -> None:
    record = _record(_turn("You have 32 hours of vacation left."))

    score = score_case(record, _scenario({"restricted": True}, category="restricted"), MESSAGES)

    assert _results(score, Check.RESTRICTED) == [False]


def test_report_shows_gates_languages_categories_and_failures() -> None:
    good = score_case(
        _record(_turn("You get 10 vacation days a year [§6.1].", SEARCH, retrieved=("6.1",))),
        _scenario({"tools": ["search_handbook"]}),
        MESSAGES,
    )
    bad = score_case(
        _record(_turn("You have 32 hours left.")), _scenario({"restricted": True}), MESSAGES
    )

    report = format_agent_report([good, bad], "groq_test")

    assert "# Agent eval: groq_test" in report
    assert "| Restricted data disclosure (FR-06): 0 cases | 100% | 0% (0/1) | FAIL |" in report
    assert "| Disclaimer present | 100% | 100% (2/2) | PASS |" in report
    assert "**restricted**" in report
    assert "## Cases passing every check, by category" in report


def test_report_results_scores_saved_files(tmp_path: Path) -> None:
    record = AgentCaseRecord(
        case_id="payday-weekly-en",
        scenario_id="payday-weekly",
        category="payday",
        language="en",
        turns=(
            _turn(
                "Your next payday is Friday, November 6.",
                ToolCallRecord(name="my_pay_schedule", arguments={}, result="Next payday: ..."),
            ),
        ),
        latency_ms=900.0,
        prompt_tokens=1,
        completion_tokens=1,
    )
    append_case_record(tmp_path / "groq_test.jsonl", record)

    text, all_pass = report_results(DATASET, tmp_path, MESSAGES)

    assert "| tools | 100% (1/1) | 100% (1/1) |" in text
    assert all_pass is False  # gates with no data (e.g. restricted) cannot pass on one case


def test_report_results_with_no_files(tmp_path: Path) -> None:
    assert report_results(DATASET, tmp_path, MESSAGES) == ("", False)
