"""Tests for roost.tools.handbook with a fake search (no database, no model)."""

from datetime import date
from typing import Any

import pytest

from roost.agent.confirmation import ConfirmationReply
from roost.agent.tools import ToolContext
from roost.core.enums import Language
from roost.evals.llm_eval import SEARCH_TOOL
from roost.rag.retrieval import SectionText
from roost.tools.handbook import SEARCH_HANDBOOK_SPEC, HandbookSearchTool, format_sections

CONTEXT = ToolContext(
    employee_id=None,
    language=Language.EN,
    today=date(2026, 11, 2),
    confirm=ConfirmationReply.from_text,
)
NOTICE = SectionText("6.4", "Vacation (PTO) > Notice required", "Two weeks' notice.")
AMOUNT = SectionText("6.1", "Vacation (PTO) > Amount", "Ten days per year.")


class FakeSearch:
    def __init__(self, sections: list[SectionText]) -> None:
        self.sections = sections
        self.queries: list[str] = []

    def __call__(self, query: str) -> list[SectionText]:
        self.queries.append(query)
        return self.sections


def test_runs_the_search_and_formats_the_sections() -> None:
    search = FakeSearch([NOTICE, AMOUNT])

    result = HandbookSearchTool(search).run({"query": "  vacation notice  "}, CONTEXT)

    assert search.queries == ["vacation notice"]  # trimmed
    assert result == (
        "[§6.4] Vacation (PTO) > Notice required\nTwo weeks' notice.\n\n"
        "[§6.1] Vacation (PTO) > Amount\nTen days per year."
    )


def test_no_results_says_so() -> None:
    assert HandbookSearchTool(FakeSearch([])).run({"query": "x"}, CONTEXT) == (
        "No matching handbook sections."
    )


@pytest.mark.parametrize(
    "arguments",
    [{}, {"query": ""}, {"query": 42}, {"query": "x" * 301}, {"q": "vacation"}],
)
def test_bad_arguments_are_explained_and_never_searched(arguments: dict[str, Any]) -> None:
    search = FakeSearch([NOTICE])

    result = HandbookSearchTool(search).run(arguments, CONTEXT)

    assert result.startswith("Error: call search_handbook with")
    assert search.queries == []


def test_blank_query_is_rejected() -> None:
    search = FakeSearch([NOTICE])

    result = HandbookSearchTool(search).run({"query": "   "}, CONTEXT)

    assert result == "Error: call search_handbook with a non-blank 'query'."
    assert search.queries == []


def test_spec_matches_what_the_phase_4_eval_measured() -> None:
    # The eval's 100% tool-use score was earned with this exact name, text and schema.
    assert SEARCH_HANDBOOK_SPEC == SEARCH_TOOL


def test_format_sections_of_nothing() -> None:
    assert format_sections([]) == "No matching handbook sections."
