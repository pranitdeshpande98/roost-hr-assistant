"""Integration tests for handbook ingestion and keyword search."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from roost.core.enums import HandbookStatus
from roost.db.models import HandbookChunk, HandbookVersion
from roost.rag.ingest import HandbookAlreadyIngestedError, ingest_handbook

pytestmark = pytest.mark.integration

HANDBOOK = Path(__file__).resolve().parents[2] / "data" / "raw" / "handbook" / "handbook-v1.0.md"


@pytest.fixture(scope="module")
def markdown() -> str:
    return HANDBOOK.read_text(encoding="utf-8")


def _ingest(session: Session, markdown: str, label: str, *, publish: bool = True) -> int:
    result = ingest_handbook(
        session,
        markdown=markdown,
        source_filename="handbook.md",
        version_label=label,
        effective_date=date(2026, 11, 1),
        publish=publish,
    )
    return result.version_id


def test_ingest_stores_every_section(db_session: Session, markdown: str) -> None:
    version_id = _ingest(db_session, markdown, "1.0")

    count = db_session.scalar(
        select(func.count())
        .select_from(HandbookChunk)
        .where(HandbookChunk.version_id == version_id)
    )
    version = db_session.get(HandbookVersion, version_id)

    assert count == 133
    assert version is not None
    assert version.status is HandbookStatus.PUBLISHED


def test_draft_is_not_published(db_session: Session, markdown: str) -> None:
    version_id = _ingest(db_session, markdown, "1.0", publish=False)

    version = db_session.get(HandbookVersion, version_id)

    assert version is not None
    assert version.status is HandbookStatus.DRAFT
    assert version.published_at is None


def test_keyword_search_finds_carryover_section(db_session: Session, markdown: str) -> None:
    _ingest(db_session, markdown, "1.0")

    top = db_session.scalar(
        text(
            "SELECT section_number FROM handbook_chunk, "
            "plainto_tsquery('english', 'carry over unused vacation next year') q "
            "WHERE search_tsv @@ q ORDER BY ts_rank(search_tsv, q) DESC LIMIT 1"
        )
    )

    assert top == "6.9"


def test_publishing_new_version_retires_previous(db_session: Session, markdown: str) -> None:
    first = _ingest(db_session, markdown, "1.0")
    second = _ingest(db_session, markdown + "\n<!-- revised -->\n", "1.1")

    statuses = dict(db_session.execute(select(HandbookVersion.id, HandbookVersion.status)).all())

    assert statuses == {first: HandbookStatus.RETIRED, second: HandbookStatus.PUBLISHED}


@pytest.mark.parametrize("change", ["same_label", "same_content"])
def test_refuses_to_ingest_twice(db_session: Session, markdown: str, change: str) -> None:
    _ingest(db_session, markdown, "1.0")
    label, content = (
        ("1.0", markdown + "\nchanged\n") if change == "same_label" else ("2.0", markdown)
    )

    with pytest.raises(HandbookAlreadyIngestedError):
        _ingest(db_session, content, label)


def test_database_allows_only_one_published_version(db_session: Session, markdown: str) -> None:
    _ingest(db_session, markdown, "1.0")
    _ingest(db_session, markdown + "\n<!-- revised -->\n", "1.1", publish=False)

    with pytest.raises(IntegrityError):
        db_session.execute(text("UPDATE handbook_version SET status = 'published'"))
    db_session.rollback()
