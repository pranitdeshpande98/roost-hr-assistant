"""Score retrieval runs: per-case outcomes rolled up into hit@k and MRR tables."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from roost.core.enums import Language
from roost.evals.dataset import RetrievalCase
from roost.evals.metrics import first_relevant_rank

ALL_LANGUAGES = "all"


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Where one search method placed the right answer for one case."""

    case_id: str
    language: Language
    method: str  # e.g. "keyword", "vector:bge-m3", "hybrid:bge-m3"
    rank: int | None  # 1-based rank of the first expected section; None if not retrieved


@dataclass(frozen=True, slots=True)
class MetricRow:
    """Aggregate scores for one method in one language (or across all languages)."""

    method: str
    language: str
    cases: int
    hit_at_k: float
    mrr: float


def score_case(case: RetrievalCase, method: str, ranked: Sequence[str]) -> CaseOutcome:
    """Record where `ranked` placed the case's expected sections."""
    return CaseOutcome(
        case_id=case.case_id,
        language=case.language,
        method=method,
        rank=first_relevant_rank(ranked, case.expected_sections),
    )


def _row(method: str, language: str, outcomes: Sequence[CaseOutcome], k: int) -> MetricRow:
    hits = sum(1 for o in outcomes if o.rank is not None and o.rank <= k)
    reciprocal = sum(1.0 / o.rank for o in outcomes if o.rank is not None)
    return MetricRow(
        method=method,
        language=language,
        cases=len(outcomes),
        hit_at_k=hits / len(outcomes),
        mrr=reciprocal / len(outcomes),
    )


def summarize(outcomes: Iterable[CaseOutcome], *, k: int) -> list[MetricRow]:
    """One row per method and language, plus an "all" row per method.

    Methods keep their first-seen order so the table reads in the order they were run.
    """
    by_method: dict[str, list[CaseOutcome]] = {}
    for outcome in outcomes:
        by_method.setdefault(outcome.method, []).append(outcome)

    rows: list[MetricRow] = []
    for method, method_outcomes in by_method.items():
        for language in Language:
            subset = [o for o in method_outcomes if o.language == language]
            if subset:
                rows.append(_row(method, language.value, subset, k))
        rows.append(_row(method, ALL_LANGUAGES, method_outcomes, k))
    return rows


def format_markdown(rows: Sequence[MetricRow], *, k: int) -> str:
    """Render rows as a Markdown table: one line per method, hit@k and MRR per language."""
    languages = [lang.value for lang in Language] + [ALL_LANGUAGES]
    lookup = {(r.method, r.language): r for r in rows}
    methods = list(dict.fromkeys(r.method for r in rows))

    header = ["method"] + [f"{lang} hit@{k}" for lang in languages] + ["all MRR"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for method in methods:
        cells = [method]
        for lang in languages:
            row = lookup.get((method, lang))
            cells.append("-" if row is None else f"{row.hit_at_k:.0%}")
        overall = lookup.get((method, ALL_LANGUAGES))
        cells.append("-" if overall is None else f"{overall.mrr:.3f}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
