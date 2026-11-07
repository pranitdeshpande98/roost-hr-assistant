"""Tests for removing citations no tool returned."""

import pytest

from roost.guardrails.citations import cited_sections, remove_unsupported_citations

SEARCH = "[§6.4] Notice\nRequest at least 14 days ahead.\n\n[§6.1] Amount\nTen days a year."
PAY = "Payroll class: Biweekly (paid every other Friday) [§5.1]."


def test_finds_citations_in_order_without_duplicates() -> None:
    assert cited_sections("[§6.4] a [§ 6.1] b [§6.4] [§13.2.1]") == ["6.4", "6.1", "13.2.1"]


def test_supported_citations_are_kept_unchanged() -> None:
    answer = "Ask 14 days ahead [§6.4]. You get ten days [§6.1]."

    assert remove_unsupported_citations(answer, [SEARCH]) == (answer, [])


def test_an_invented_citation_is_removed_and_the_sentence_kept() -> None:
    answer = "Ask 14 days ahead [§6.4]. Talk to your supervisor [§2.3]."

    cleaned, removed = remove_unsupported_citations(answer, [SEARCH])

    assert cleaned == "Ask 14 days ahead [§6.4]. Talk to your supervisor."
    assert removed == ["2.3"]


def test_a_parent_section_of_a_returned_one_is_supported() -> None:
    answer = "That is in the vacation policy [§6]."

    assert remove_unsupported_citations(answer, [SEARCH])[1] == []


def test_a_child_or_sibling_is_not_supported_by_its_parent() -> None:
    cleaned, removed = remove_unsupported_citations("See [§6.7] and [§16].", ["[§6] Vacation"])

    assert cleaned == "See and."  # only the brackets go; the model's wording stays
    assert removed == ["6.7", "16"]


def test_citations_from_any_tool_count() -> None:
    answer = "You are paid every other Friday [§5.1]; ask early [§6.4]."

    assert remove_unsupported_citations(answer, [PAY, SEARCH])[1] == []


@pytest.mark.parametrize("sources", [[], [""], ["no citations here"]])
def test_without_returned_sections_every_citation_goes(sources: list[str]) -> None:
    cleaned, removed = remove_unsupported_citations("Ten days [§6.1]. Fourteen [§6.4].", sources)

    assert cleaned == "Ten days. Fourteen."
    assert removed == ["6.1", "6.4"]


def test_line_breaks_and_lists_survive_cleaning() -> None:
    answer = "Steps:\n- Call HR [§2.3]\n- Fill the form [§16.2]"

    cleaned, _ = remove_unsupported_citations(answer, ["[§16.2] Report"])

    assert cleaned == "Steps:\n- Call HR\n- Fill the form [§16.2]"


def test_citations_inside_the_sentence_are_checked_too() -> None:
    # Seen in the Phase 7 agent eval: "(xem §13.2)" slipped past a brackets-only check.
    answer = (
        "Gọi đường dây chấm công (xem §13.2). Có thể cần giấy chứng nhận (xem §11.3). "
        "Details in §8.3 and §4.2. [§8.3]"
    )

    cleaned, removed = remove_unsupported_citations(answer, ["[§11.3] FMLA", "[§8.3] Notice"])

    assert cleaned == (
        "Gọi đường dây chấm công. Có thể cần giấy chứng nhận (xem §11.3). "
        "Details in §8.3 and. [§8.3]"
    )
    assert removed == ["13.2", "4.2"]


def test_every_citation_form_is_found() -> None:
    assert cited_sections("[§6.4] (see §6.1) ver §5.2, [§ 6.4]") == ["6.4", "6.1", "5.2"]
