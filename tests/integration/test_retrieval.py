"""Integration tests for roost.rag.retrieval against the real handbook."""

import hashlib
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.db.models import ChunkEmbedding, HandbookChunk
from roost.rag.ingest import ingest_handbook
from roost.rag.retrieval import hybrid_search, keyword_search, vector_search

pytestmark = pytest.mark.integration

HANDBOOK = Path(__file__).resolve().parents[2] / "data" / "raw" / "handbook" / "handbook-v1.0.md"
MODEL = "fake-model"


def _vector(text: str) -> list[float]:
    """Deterministic 8-dimensional vector per text, so every section gets its own."""
    return [byte / 255 for byte in hashlib.sha256(text.encode("utf-8")).digest()[:8]]


@pytest.fixture
def handbook(db_session: Session) -> Session:
    """Published handbook with a fake embedding stored for every chunk."""
    ingest_handbook(
        db_session,
        markdown=HANDBOOK.read_text(encoding="utf-8"),
        source_filename=HANDBOOK.name,
        version_label="1.0",
        effective_date=date(2026, 11, 1),
        publish=True,
    )
    chunks = db_session.scalars(select(HandbookChunk)).all()
    db_session.add_all(
        ChunkEmbedding(chunk_id=c.id, model_name=MODEL, embedding=_vector(c.section_number))
        for c in chunks
    )
    db_session.flush()
    return db_session


def test_keyword_search_finds_the_matching_section(handbook: Session) -> None:
    results = keyword_search(handbook, "bereavement leave for a funeral", limit=5)

    assert results
    assert set(results) & {"9", "9.1", "9.2", "9.3", "9.4"}


def test_keyword_search_matches_any_term_not_all(handbook: Session) -> None:
    # "zebra" is in no section; with AND matching this would return nothing.
    assert keyword_search(handbook, "bereavement zebra", limit=5)


def test_keyword_search_respects_the_limit(handbook: Session) -> None:
    assert len(keyword_search(handbook, "leave vacation sick pay", limit=3)) == 3


def test_vector_search_ranks_the_nearest_section_first(handbook: Session) -> None:
    results = vector_search(handbook, MODEL, _vector("6.4"), limit=5)

    assert results[0] == "6.4"
    assert len(results) == 5


def test_vector_search_only_uses_the_requested_model(handbook: Session) -> None:
    assert vector_search(handbook, "other-model", _vector("6.4"), limit=5) == []


def test_hybrid_search_includes_both_signals(handbook: Session) -> None:
    keyword_top = keyword_search(handbook, "bereavement", limit=1)[0]

    results = hybrid_search(handbook, MODEL, "bereavement", _vector("6.4"), limit=10)

    assert len(results) == 10
    assert {"6.4", keyword_top} <= set(results)


def test_searches_ignore_unpublished_handbooks(db_session: Session) -> None:
    ingest_handbook(
        db_session,
        markdown=HANDBOOK.read_text(encoding="utf-8"),
        source_filename=HANDBOOK.name,
        version_label="1.0",
        effective_date=date(2026, 11, 1),
        publish=False,
    )

    assert keyword_search(db_session, "bereavement", limit=5) == []
