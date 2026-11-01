"""Tests for roost.evals.retrieval_eval."""

import pytest

from roost.core.enums import Language
from roost.evals.dataset import RetrievalCase
from roost.evals.retrieval_eval import (
    ALL_LANGUAGES,
    CaseOutcome,
    MetricRow,
    format_markdown,
    score_case,
    summarize,
)


def _case(language: Language) -> RetrievalCase:
    return RetrievalCase(
        case_id=f"vacation-notice-{language.value}",
        intent="vacation-notice",
        language=language,
        question="How far ahead do I ask for time off?",
        expected_sections=("6.4",),
    )


def _outcome(method: str, language: Language, rank: int | None) -> CaseOutcome:
    return CaseOutcome(case_id="c", language=language, method=method, rank=rank)


def test_score_case_records_rank_of_expected_section() -> None:
    outcome = score_case(_case(Language.ES), "vector:m", ["7.1", "6.4"])

    assert outcome == CaseOutcome(
        case_id="vacation-notice-es", language=Language.ES, method="vector:m", rank=2
    )


def test_score_case_records_none_when_not_retrieved() -> None:
    assert score_case(_case(Language.EN), "keyword", ["7.1"]).rank is None


def test_summarize_scores_each_language_and_overall() -> None:
    outcomes = [
        _outcome("vector:m", Language.EN, 1),
        _outcome("vector:m", Language.EN, 6),  # outside top 5: a miss for hit@5
        _outcome("vector:m", Language.ES, 2),
        _outcome("vector:m", Language.VI, None),
    ]

    rows = {row.language: row for row in summarize(outcomes, k=5)}

    assert rows[Language.EN.value] == MetricRow(
        method="vector:m", language="en", cases=2, hit_at_k=0.5, mrr=pytest.approx((1 + 1 / 6) / 2)
    )
    assert rows[Language.ES.value].hit_at_k == 1.0
    assert rows[Language.VI.value].mrr == 0.0
    assert rows[ALL_LANGUAGES].cases == 4
    assert rows[ALL_LANGUAGES].hit_at_k == pytest.approx(2 / 4)


def test_summarize_keeps_methods_in_run_order() -> None:
    outcomes = [_outcome("keyword", Language.EN, 1), _outcome("hybrid:m", Language.EN, 1)]

    methods = list(dict.fromkeys(row.method for row in summarize(outcomes, k=5)))

    assert methods == ["keyword", "hybrid:m"]


def test_summarize_skips_languages_without_cases() -> None:
    rows = summarize([_outcome("keyword", Language.EN, 1)], k=5)

    assert [row.language for row in rows] == ["en", ALL_LANGUAGES]


def test_format_markdown_renders_one_line_per_method() -> None:
    outcomes = [
        _outcome("keyword", Language.EN, 1),
        _outcome("keyword", Language.ES, None),
        _outcome("keyword", Language.VI, 2),
    ]

    table = format_markdown(summarize(outcomes, k=5), k=5)

    assert table.splitlines() == [
        "| method | en hit@5 | es hit@5 | vi hit@5 | all hit@5 | all MRR |",
        "|---|---|---|---|---|---|",
        "| keyword | 100% | 0% | 100% | 67% | 0.500 |",
    ]


def test_format_markdown_marks_missing_languages() -> None:
    table = format_markdown(summarize([_outcome("keyword", Language.EN, 1)], k=3), k=3)

    assert table.splitlines()[-1] == "| keyword | 100% | - | - | 100% | 1.000 |"
