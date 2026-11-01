"""Unit tests for roost.rag.parsing."""

from pathlib import Path

import pytest

from roost.rag.parsing import HandbookParseError, HandbookSection, parse_handbook

HANDBOOK = Path(__file__).resolve().parents[3] / "data" / "raw" / "handbook" / "handbook-v1.0.md"


@pytest.fixture(scope="module")
def sections() -> list[HandbookSection]:
    return parse_handbook(HANDBOOK.read_text(encoding="utf-8"))


def _by_number(sections: list[HandbookSection]) -> dict[str, HandbookSection]:
    return {s.number: s for s in sections}


def test_real_handbook_has_all_sections(sections: list[HandbookSection]) -> None:
    numbers = [s.number for s in sections]

    assert len(sections) == 133  # 20 sections + 113 subsections
    assert numbers[0] == "1"
    assert numbers[-1] == "20"
    assert {str(n) for n in range(1, 21)} <= set(numbers)


def test_subsection_text_and_heading_path(sections: list[HandbookSection]) -> None:
    notice = _by_number(sections)["6.4"]

    assert notice.title == "Notice required"
    assert notice.heading_path == "6. Vacation (PTO) > 6.4 Notice required"
    assert "14 calendar days" in notice.content


def test_section_introduction_is_its_own_chunk(sections: list[HandbookSection]) -> None:
    vacation = _by_number(sections)["6"]

    assert vacation.heading_path == "6. Vacation (PTO)"
    assert "10 vacation days" in vacation.content
    assert "### 6.1" not in vacation.content


def test_tables_are_kept_as_markdown(sections: list[HandbookSection]) -> None:
    premiums = _by_number(sections)["14.5"]

    assert "| Medical Gold | $80 | $120 | $150 |" in premiums.content


def test_text_before_first_section_is_ignored(sections: list[HandbookSection]) -> None:
    assert all("portfolio project and is not legal advice" not in s.content for s in sections)


def test_double_digit_subsections_parse() -> None:
    markdown = "## 6. Vacation\n\nIntro.\n\n### 6.10 Leaving\n\nPaid out by state law.\n"

    numbers = [s.number for s in parse_handbook(markdown)]

    assert numbers == ["6", "6.10"]


def test_section_without_intro_text_is_skipped() -> None:
    markdown = "## 2. Contacts\n\n### 2.1 Phones\n\nCall HR.\n"

    assert [s.number for s in parse_handbook(markdown)] == ["2.1"]


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        ("# Title only\n\nNo numbered sections.\n", "No numbered sections"),
        ("## 6. Vacation\n\nText.\n\n### 7.1 Wrong parent\n\nText.\n", "not under section 6"),
        ("### 1.1 Orphan\n\nText.\n", "not under section"),
        ("## 6.1 Bad top level\n\nText.\n", "subsection number"),
        ("## 6. A\n\nText.\n\n## 6. B\n\nText.\n", "Duplicate section numbers: 6"),
    ],
)
def test_rejects_inconsistent_structure(markdown: str, message: str) -> None:
    with pytest.raises(HandbookParseError, match=message):
        parse_handbook(markdown)
