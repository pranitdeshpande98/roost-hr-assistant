"""Tests for guardrail eval measures, report and storage."""

from pathlib import Path

from roost.core.enums import Language
from roost.evals.guardrail_eval import (
    ALLOWED_KEPT,
    EMERGENCY_RECALL,
    INJECTION_BLOCKED,
    SENSITIVE_PRECISION,
    SENSITIVE_RECALL,
    GuardrailRecord,
    append_guardrail_record,
    format_guardrail_report,
    gates_pass,
    load_guardrail_records,
    remove_guardrail_records,
)
from roost.guardrails.classifier import Source
from roost.guardrails.types import Category

H, V, D, INJ, OWN, N = (
    Category.HARASSMENT,
    Category.VIOLENCE,
    Category.DANGER,
    Category.INJECTION,
    Category.OWN_DATA,
    Category.NONE,
)


def _record(
    expected: Category,
    predicted: Category,
    *,
    case_id: str = "c-en",
    language: Language = Language.EN,
    tag: str | None = None,
) -> GuardrailRecord:
    return GuardrailRecord(
        case_id=case_id,
        split="dev",
        language=language,
        tag=tag if expected is N else None,
        text="message",
        expected=expected,
        predicted=predicted,
        source=Source.LLM,
        reason="fake",
        latency_ms=1.0,
    )


def test_a_different_sensitive_category_still_counts_as_routed() -> None:
    records = [_record(H, V), _record(D, H)]  # harassment seen as violence: same contacts

    assert SENSITIVE_RECALL.rate(records) == (2, 2)
    assert EMERGENCY_RECALL.rate(records) == (0, 1)  # but an emergency must get 911 first


def test_precision_counts_allowed_messages_routed_to_hr() -> None:
    records = [_record(H, H), _record(N, H, tag="procedure"), _record(N, N, tag="policy")]

    assert SENSITIVE_PRECISION.rate(records) == (1, 2)
    assert ALLOWED_KEPT.rate(records) == (1, 2)


def test_an_injection_is_blocked_by_any_route_but_allowed() -> None:
    records = [_record(INJ, INJ), _record(INJ, OWN), _record(INJ, N)]

    assert INJECTION_BLOCKED.rate(records) == (2, 3)


def test_gates_need_data_and_every_threshold() -> None:
    perfect = [_record(H, H), _record(INJ, INJ), _record(N, N, tag="small_talk")]

    assert gates_pass(perfect)
    assert not gates_pass([*perfect, _record(H, N)])  # one miss in 2: 50% recall
    assert not gates_pass([_record(N, N, tag="small_talk")])  # no sensitive data at all


def test_report_lists_gates_languages_kinds_and_wrong_routes() -> None:
    records = [
        _record(H, H),
        _record(H, N, case_id="miss-es", language=Language.ES),
        _record(N, OWN, case_id="blocked-en", tag="policy"),
    ]

    report = format_guardrail_report(records, "dev split")

    assert "| Sensitive-topic routing recall | 98% | 50% (1/2) | FAIL |" in report
    assert "| policy | 0% (0/1) |" in report
    assert "`miss-es` sensitive -> **allowed**" in report
    assert "`blocked-en` allowed -> **own_data**" in report
    assert "LLM answers by model: fake 3" in report
    assert all(line == line.rstrip() for line in report.splitlines())


def test_records_round_trip_and_can_be_removed(tmp_path: Path) -> None:
    path = tmp_path / "g.jsonl"
    first, second = _record(H, H, case_id="a-en"), _record(N, N, case_id="b-en", tag="tool")
    append_guardrail_record(path, first)
    append_guardrail_record(path, second)

    assert load_guardrail_records(path) == [first, second]
    assert remove_guardrail_records(path, ["a-en"]) == 1
    assert load_guardrail_records(path) == [second]
    assert b"\r" not in path.read_bytes()
    assert load_guardrail_records(tmp_path / "missing.jsonl") == []
