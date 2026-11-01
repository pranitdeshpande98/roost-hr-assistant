"""Tests for roost.evals.llm_eval, using a scripted model and a fake search."""

from pathlib import Path

import pytest

from roost.core.enums import Language
from roost.evals.dataset import RetrievalCase
from roost.evals.llm_eval import (
    SEARCH_TOOL,
    LLMCaseRecord,
    append_record,
    format_llm_report,
    format_sections,
    load_records,
    parse_citations,
    run_case,
    score_record,
)
from roost.llm.errors import LLMResponseError, LLMUnavailableError
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall
from roost.rag.retrieval import SectionText

CASE = RetrievalCase(
    case_id="vacation-notice-es",
    intent="vacation-notice",
    language=Language.ES,
    question="¿Con cuánta anticipación tengo que pedir días libres?",
    expected_sections=("6.4",),
)
NOTICE = SectionText("6.4", "Vacation (PTO) > Notice required", "Two weeks' notice.")
SEARCH_CALL = ToolCall(id="c1", name="search_handbook", arguments={"query": "vacation notice"})


class FakeSearch:
    def __init__(self, sections: list[SectionText]) -> None:
        self.sections = sections
        self.queries: list[str] = []

    def __call__(self, query: str) -> list[SectionText]:
        self.queries.append(query)
        return self.sections


def _record(**overrides: object) -> LLMCaseRecord:
    fields: dict[str, object] = {
        "case_id": CASE.case_id,
        "intent": CASE.intent,
        "language": Language.ES,
        "expected_sections": ("6.4",),
        "model": "fake/m",
        "tool_called_first": True,
        "retrieved_sections": ("6.4", "6.5"),
        "answer": "Debe pedir sus días con dos semanas de anticipación [§6.4].",
    }
    fields.update(overrides)
    return LLMCaseRecord.model_validate(fields)


# --- run_case -------------------------------------------------------------------------


def test_full_tool_loop_records_search_and_answer() -> None:
    model = ScriptedChatModel([tool_reply(SEARCH_CALL), text_reply("Dos semanas [§6.4].")])
    search = FakeSearch([NOTICE])

    record = run_case(model, CASE, search)

    assert record.tool_called_first is True
    assert record.tool_queries == ("vacation notice",)
    assert record.retrieved_sections == ("6.4",)
    assert record.answer == "Dos semanas [§6.4]."
    assert record.turns == 2
    assert record.error is None
    # Turn 1 offers the tool; turn 2 asks for the answer and includes the tool result.
    assert model.requests[0].tools == [SEARCH_TOOL]
    assert model.requests[1].tools is None
    tool_message = model.requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "c1"
    assert "[§6.4]" in (tool_message.content or "")


def test_answering_without_searching_is_recorded() -> None:
    model = ScriptedChatModel([text_reply("Dos semanas.")])

    record = run_case(model, CASE, FakeSearch([NOTICE]))

    assert record.tool_called_first is False
    assert record.tool_queries == ()
    assert record.answer == "Dos semanas."


def test_endless_tool_calls_stop_at_max_turns() -> None:
    model = ScriptedChatModel([tool_reply(SEARCH_CALL), tool_reply(SEARCH_CALL)])

    record = run_case(model, CASE, FakeSearch([NOTICE]), max_turns=2)

    assert record.answer is None
    assert record.error == "no final answer after 2 turns"


def test_bad_model_output_becomes_an_error_record() -> None:
    model = ScriptedChatModel([LLMResponseError("tool 'search_handbook': not JSON")])

    record = run_case(model, CASE, FakeSearch([NOTICE]))

    assert record.error == "tool 'search_handbook': not JSON"


def test_unavailable_model_propagates_so_the_run_can_stop() -> None:
    model = ScriptedChatModel([LLMUnavailableError("rate limited")])

    with pytest.raises(LLMUnavailableError):
        run_case(model, CASE, FakeSearch([NOTICE]))


def test_missing_query_argument_falls_back_to_the_question() -> None:
    call = ToolCall(id="c1", name="search_handbook", arguments={})
    model = ScriptedChatModel([tool_reply(call), text_reply("ok")])
    search = FakeSearch([])

    run_case(model, CASE, search)

    assert search.queries == [CASE.question]


# --- helpers ------------------------------------------------------------------------


def test_format_sections_shows_numbers_and_headings() -> None:
    assert format_sections([NOTICE]) == (
        "[§6.4] Vacation (PTO) > Notice required\nTwo weeks' notice."
    )
    assert format_sections([]) == "No matching handbook sections."


@pytest.mark.parametrize(
    ("text", "cited"),
    [
        ("See [§6.4] and [§6.5].", ("6.4", "6.5")),
        ("Per §9 and § 9.2, and again [§9].", ("9", "9.2")),
        ("No citations here.", ()),
        (None, ()),
    ],
)
def test_parse_citations(text: str | None, cited: tuple[str, ...]) -> None:
    assert parse_citations(text) == cited


# --- scoring ------------------------------------------------------------------------


def test_good_answer_passes_every_check() -> None:
    score = score_record(_record())

    assert not score.errored
    assert score.used_tool
    assert score.language_match
    assert score.cites_retrieved
    assert score.cites_expected


def test_wrong_language_fails_language_match() -> None:
    score = score_record(_record(answer="You need two weeks of notice [§6.4]."))

    assert not score.language_match
    assert score.cites_expected


def test_citing_an_unretrieved_section_is_not_valid() -> None:
    score = score_record(_record(answer="Debe pedir con dos semanas [§6.4] y [§7.1]."))

    assert not score.cites_retrieved  # §7.1 was never shown to the model
    assert score.cites_expected


def test_no_citation_fails_both_citation_checks() -> None:
    score = score_record(_record(answer="Debe pedir sus días con dos semanas."))

    assert not score.cites_retrieved
    assert not score.cites_expected


def test_errored_record_fails_language_match() -> None:
    score = score_record(_record(answer=None, error="no final answer after 3 turns"))

    assert score.errored
    assert not score.language_match


# --- storage and report -------------------------------------------------------------


def test_records_round_trip_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "results" / "m.jsonl"
    first = _record()
    second = _record(case_id="vacation-notice-en", language=Language.EN)

    append_record(path, first)
    append_record(path, second)

    assert load_records(path) == [first, second]


def test_load_records_of_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_records(tmp_path / "nothing.jsonl") == []


def test_records_are_written_with_lf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"

    append_record(path, _record())
    append_record(path, _record(case_id="vacation-notice-en"))

    raw = path.read_bytes()
    assert b"\r" not in raw  # meaningful on Windows, where text mode would write CRLF
    assert raw.count(b"\n") == 2


def test_report_shows_pass_rates_speed_and_tokens() -> None:
    scores = [
        score_record(_record(latency_ms=1000, prompt_tokens=900, completion_tokens=100)),
        score_record(
            _record(
                answer="You need two weeks [§6.4].",
                latency_ms=3000,
                prompt_tokens=1100,
                completion_tokens=100,
            )
        ),
    ]

    table = format_llm_report(scores).splitlines()

    assert table[0] == (
        "| model | cases | errors | searched | right language | valid cites | "
        "correct section | median s | p90 s | tokens/case |"
    )
    assert table[2] == "| fake/m | 2 | 0% | 100% | 50% | 100% | 100% | 1.0 | 3.0 | 1100 |"


def test_report_ignores_failed_cases_for_speed() -> None:
    scores = [
        score_record(_record(latency_ms=2000)),
        score_record(_record(answer=None, error="no choices", latency_ms=0)),
    ]

    row = format_llm_report(scores).splitlines()[2]

    assert row.endswith("| 2.0 | 2.0 | 0 |")
    assert "| 50% |" in row  # one of two cases errored


def test_report_marks_models_with_no_answers() -> None:
    row = format_llm_report([score_record(_record(answer=None, error="x"))]).splitlines()[2]

    assert row.endswith("| - | - | - |")
