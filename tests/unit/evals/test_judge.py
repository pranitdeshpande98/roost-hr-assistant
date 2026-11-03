"""Tests for roost.evals.judge with a scripted judge (no real model)."""

from datetime import date
from pathlib import Path

import pytest

from roost.agent.messages import default_messages
from roost.core.enums import Language
from roost.evals.agent_dataset import Scenario
from roost.evals.agent_harness import AgentCaseRecord, ToolCallRecord, TurnRecord
from roost.evals.agent_scoring import Check, answer_text, format_agent_report, score_case
from roost.evals.judge import (
    Judgment,
    Verdict,
    agreement,
    append_judgment,
    date_context,
    judge_answer,
    judgeable_turns,
    judgments_path,
    load_calibration,
    load_judgments,
    parse_verdict,
    remove_judgments,
    tool_source,
)
from roost.llm.errors import LLMResponseError
from roost.llm.fake import ScriptedChatModel, text_reply

CALIBRATION = (
    Path(__file__).resolve().parents[3] / "evals" / "datasets" / "judge_calibration_v1.yaml"
)
MESSAGES = default_messages()
GOOD = '{"faithful": true, "unsupported_claims": [], "citations_correct": true, "bad_citations": [], "reason": "ok"}'
BAD = '{"faithful": false, "unsupported_claims": ["12 days"], "citations_correct": false, "bad_citations": ["§6"], "reason": "wrong number"}'


# --- parsing and asking -------------------------------------------------------------


def test_parses_a_clean_verdict() -> None:
    verdict = parse_verdict(BAD)

    assert verdict.faithful is False
    assert verdict.unsupported_claims == ("12 days",)
    assert verdict.bad_citations == ("§6",)


def test_finds_the_json_inside_extra_text() -> None:
    assert parse_verdict(f"Here is my verdict:\n{GOOD}\nThanks.").faithful is True


@pytest.mark.parametrize("reply", ["no json here", '{"faithful": "maybe"}', "{not json}", None])
def test_malformed_replies_raise(reply: str | None) -> None:
    with pytest.raises(LLMResponseError):
        parse_verdict(reply)


def test_judge_gets_sources_and_answer_at_temperature_zero() -> None:
    judge = ScriptedChatModel([text_reply(GOOD)])

    judge_answer(judge, "You get 10 days [§6].", ["[§6] Vacation\nTen days."])

    request = judge.requests[0]
    assert request.temperature == 0.0
    assert "SOURCES:\n[§6] Vacation\nTen days." in (request.messages[1].content or "")
    assert (request.messages[1].content or "").endswith("ANSWER:\nYou get 10 days [§6].")


def test_a_malformed_reply_is_retried_once() -> None:
    judge = ScriptedChatModel([text_reply("oops"), text_reply(GOOD)])

    assert judge_answer(judge, "a", ["s"]).faithful is True
    assert len(judge.requests) == 2


def test_gives_up_after_the_retry() -> None:
    judge = ScriptedChatModel([text_reply("oops"), text_reply("still oops")])

    with pytest.raises(LLMResponseError):
        judge_answer(judge, "a", ["s"])


# --- calibration --------------------------------------------------------------------


def test_calibration_set_is_balanced() -> None:
    items = load_calibration(CALIBRATION)

    assert len(items) == 23
    assert sum(i.faithful and i.citations_correct for i in items) == 12
    assert sum(not i.faithful for i in items) == 7
    assert sum(not i.citations_correct for i in items) == 7
    assert sum(bool(i.context) for i in items) == 2  # date calculations: one right, one wrong


def test_a_judge_matching_every_label_agrees_fully() -> None:
    items = load_calibration(CALIBRATION)
    verdicts = [Verdict(faithful=i.faithful, citations_correct=i.citations_correct) for i in items]

    result = agreement(items, verdicts)

    assert (result.faithful_agreement, result.citation_agreement) == (1.0, 1.0)
    assert result.caught_unfaithful == 1.0
    assert result.disagreements == ()


def test_a_judge_that_misses_an_error_is_caught() -> None:
    items = load_calibration(CALIBRATION)
    always_fine = [Verdict(faithful=True, citations_correct=True) for _ in items]

    result = agreement(items, always_fine)

    assert result.caught_unfaithful == 0.0  # it missed all seven planted factual errors
    assert result.faithful_agreement == pytest.approx(16 / 23)
    assert len(result.disagreements) == 11


# --- which turns get judged ---------------------------------------------------------


def _turn(reply: str, *results: str, paused: bool = False) -> TurnRecord:
    calls = tuple(ToolCallRecord(name="search_handbook", arguments={}, result=r) for r in results)
    return TurnRecord(say="q", reply=reply, paused=paused, tool_calls=calls)


def _record(*turns: TurnRecord, language: str = "en") -> AgentCaseRecord:
    return AgentCaseRecord(
        case_id="c-en",
        scenario_id="c",
        category="handbook",
        language=language,
        turns=turns,
        latency_ms=1,
        prompt_tokens=0,
        completion_tokens=0,
    )


def _strip(reply: str) -> str:
    return answer_text(reply, Language.EN, MESSAGES)


def test_judges_answers_against_every_source_so_far() -> None:
    disclaimer = MESSAGES.get("disclaimer", Language.EN)
    record = _record(
        _turn(f"Ten days [§6].\n\n{disclaimer}", "[§6] Ten days."),
        _turn(f"Give 14 days' notice.\n\n{disclaimer}", "[§6.4] Fourteen days."),
    )

    turns = judgeable_turns(record, _strip)

    first = "Tool search_handbook called with {} returned:\n[§6] Ten days."
    second = "Tool search_handbook called with {} returned:\n[§6.4] Fourteen days."
    assert turns[0] == (1, "Ten days [§6].", [first])
    assert turns[1][2] == [first, second]  # sources accumulate


def test_a_tool_source_shows_the_arguments_and_the_result() -> None:
    call = ToolCallRecord(
        name="request_vacation",
        arguments={"days": [{"date": "2026-12-14", "portion": "full"}]},
        result="SUBMITTED. Reference VR-ABC234.",
    )

    assert tool_source(call) == (
        'Tool request_vacation called with {"days": [{"date": "2026-12-14", "portion": "full"}]}'
        " returned:\nSUBMITTED. Reference VR-ABC234."
    )


def test_date_context_matches_the_agent_prompt() -> None:
    assert date_context(date(2026, 11, 2)) == "Today is 2026-11-02 (Monday)."


def test_the_judge_sees_the_context_before_the_sources() -> None:
    judge = ScriptedChatModel([text_reply(GOOD)])

    judge_answer(judge, "Next Monday is 7 days away.", ["SRC"], "Today is 2026-11-02 (Monday).")

    content = judge.requests[0].messages[1].content or ""
    assert content.startswith("CONTEXT:\nToday is 2026-11-02 (Monday).\n\nSOURCES:\nSRC")


def test_no_context_section_without_context() -> None:
    judge = ScriptedChatModel([text_reply(GOOD)])

    judge_answer(judge, "Ten days.", ["SRC"])

    assert (judge.requests[0].messages[1].content or "").startswith("SOURCES:")


def test_skips_confirmation_questions_and_turns_without_sources() -> None:
    record = _record(
        _turn("What's the weather?"),  # no sources yet: left to deterministic checks
        _turn("Please confirm", "SUMMARY", paused=True),  # a fixed template, not an answer
    )

    assert judgeable_turns(record, _strip) == []


def test_judgments_round_trip_in_their_own_folder(tmp_path: Path) -> None:
    path = judgments_path(tmp_path, "groq_chain")
    judgment = Judgment(case_id="c-en", turn=1, judge="j", verdict=parse_verdict(GOOD))

    append_judgment(path, judgment)

    assert path.parent.name == "judgments"  # not picked up as a results file
    assert load_judgments(path) == [judgment]
    assert load_judgments(tmp_path / "missing.jsonl") == []


def test_removes_judgments_of_some_cases_or_all(tmp_path: Path) -> None:
    path = judgments_path(tmp_path, "chain")
    for case_id in ("a-en", "b-en", "a-es"):
        append_judgment(path, Judgment(case_id=case_id, turn=1, judge="j", error="x"))

    assert remove_judgments(path, {"a-en", "a-es"}) == 2
    assert [j.case_id for j in load_judgments(path)] == ["b-en"]
    assert remove_judgments(path) == 1
    assert load_judgments(path) == []
    assert remove_judgments(tmp_path / "missing.jsonl") == 0


# --- verdicts in scoring and the report ---------------------------------------------


def _scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "c",
            "category": "handbook",
            "employee": "office_biweekly",
            "turns": [{"say": {"en": "q", "es": "q", "vi": "q"}}],
        }
    )


def test_verdicts_become_checks_and_judge_errors_are_not_counted() -> None:
    disclaimer = MESSAGES.get("disclaimer", Language.EN)
    record = _record(_turn(f"You get 12 days [§6].\n\n{disclaimer}", "[§6] Ten days."))
    judgments = [
        Judgment(case_id="c-en", turn=1, judge="j", verdict=parse_verdict(BAD)),
        Judgment(case_id="c-en", turn=1, judge="j", error="no JSON"),
    ]

    score = score_case(record, _scenario(), MESSAGES, judgments)

    faithful = [c for c in score.checks if c.check is Check.FAITHFUL]
    citations = [c for c in score.checks if c.check is Check.CITATIONS]
    assert [c.passed for c in faithful] == [False]
    assert "12 days" in faithful[0].detail
    assert [c.passed for c in citations] == [False]


def test_report_includes_the_judge_gates() -> None:
    disclaimer = MESSAGES.get("disclaimer", Language.EN)
    record = _record(_turn(f"Ten days [§6].\n\n{disclaimer}", "[§6] Ten days."))
    judgment = Judgment(case_id="c-en", turn=1, judge="j", verdict=parse_verdict(GOOD))

    report = format_agent_report([score_case(record, _scenario(), MESSAGES, [judgment])], "x")

    assert (
        "| Answer faithfulness to retrieved handbook text (LLM judge) | 90% | 100% (1/1) | PASS |"
        in report
    )
    assert "| Citation correctness (LLM judge) | 90% | 100% (1/1) | PASS |" in report
