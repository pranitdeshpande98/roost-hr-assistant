"""Unit tests for roost.evals.dataset and the retrieval dataset file."""

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from roost.core.enums import Language
from roost.evals.dataset import RetrievalCase, load_retrieval_cases
from roost.rag.parsing import parse_handbook

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "evals" / "datasets" / "retrieval_v1.yaml"
HANDBOOK = ROOT / "data" / "raw" / "handbook" / "handbook-v1.0.md"


@pytest.fixture(scope="module")
def cases() -> list[RetrievalCase]:
    return load_retrieval_cases(DATASET)


def test_dataset_has_thirty_intents_in_three_languages(cases: list[RetrievalCase]) -> None:
    assert len(cases) == 90
    assert Counter(c.language for c in cases) == {Language.EN: 30, Language.ES: 30, Language.VI: 30}


def test_case_ids_are_unique(cases: list[RetrievalCase]) -> None:
    ids = [c.case_id for c in cases]

    assert len(set(ids)) == len(ids)


def test_every_expected_section_exists_in_handbook(cases: list[RetrievalCase]) -> None:
    handbook_sections = {s.number for s in parse_handbook(HANDBOOK.read_text(encoding="utf-8"))}

    unknown = {s for c in cases for s in c.expected_sections} - handbook_sections

    assert not unknown, f"sections not in handbook: {sorted(unknown)}"


def test_translations_share_the_same_answer(cases: list[RetrievalCase]) -> None:
    answers_by_intent: dict[str, set[tuple[str, ...]]] = {}
    for case in cases:
        answers_by_intent.setdefault(case.intent, set()).add(case.expected_sections)

    assert all(len(answers) == 1 for answers in answers_by_intent.values())


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "dataset.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_rejects_missing_language(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "- id: a\n  expected_sections: ['6.9']\n  questions:\n    en: Q\n    es: P\n",
    )

    with pytest.raises(ValidationError, match="missing languages: vi"):
        load_retrieval_cases(path)


def test_rejects_badly_formatted_section(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "- id: a\n  expected_sections: ['six']\n  questions: {en: Q, es: P, vi: C}\n",
    )

    with pytest.raises(ValidationError, match="expected_sections"):
        load_retrieval_cases(path)


def test_rejects_duplicate_intent_ids(tmp_path: Path) -> None:
    entry = "- id: a\n  expected_sections: ['6.9']\n  questions: {en: Q, es: P, vi: C}\n"
    path = _write(tmp_path, entry + entry)

    with pytest.raises(ValueError, match="duplicate intent ids: a"):
        load_retrieval_cases(path)


def test_rejects_non_list_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "id: a\n")

    with pytest.raises(ValueError, match="expected a list"):
        load_retrieval_cases(path)


def test_rejects_empty_question(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "- id: a\n  expected_sections: ['6.9']\n  questions: {en: Q, es: P, vi: '  '}\n",
    )

    with pytest.raises(ValidationError, match="must not be empty"):
        load_retrieval_cases(path)
