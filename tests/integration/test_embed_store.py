"""Integration tests for roost.rag.embed_store, using a fake embedding model."""

import hashlib
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roost.db.models import ChunkEmbedding
from roost.rag.embed_store import NoPublishedHandbookError, embed_published_handbook
from roost.rag.ingest import ingest_handbook

pytestmark = pytest.mark.integration

HANDBOOK = Path(__file__).resolve().parents[2] / "data" / "raw" / "handbook" / "handbook-v1.0.md"


class HashingEmbedder:
    """Deterministic 8-dimensional vectors derived from the text. No model needed."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.documents_seen = 0

    @property
    def model_name(self) -> str:
        return self._name

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:8]]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.documents_seen += len(texts)
        return [self._vector(t) for t in texts]

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


@pytest.fixture
def published(db_session: Session) -> Session:
    ingest_handbook(
        db_session,
        markdown=HANDBOOK.read_text(encoding="utf-8"),
        source_filename=HANDBOOK.name,
        version_label="1.0",
        effective_date=date(2026, 11, 1),
        publish=True,
    )
    return db_session


def _stored(session: Session, model_name: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ChunkEmbedding)
            .where(ChunkEmbedding.model_name == model_name)
        )
        or 0
    )


def test_embeds_every_published_chunk(published: Session) -> None:
    embedder = HashingEmbedder("fake-a")

    count = embed_published_handbook(published, embedder, batch_size=50)

    assert count == 133
    assert embedder.documents_seen == 133
    assert _stored(published, "fake-a") == 133


def test_rerun_skips_chunks_already_embedded(published: Session) -> None:
    embed_published_handbook(published, HashingEmbedder("fake-a"))
    second = HashingEmbedder("fake-a")

    assert embed_published_handbook(published, second) == 0
    assert second.documents_seen == 0


def test_models_are_stored_side_by_side(published: Session) -> None:
    embed_published_handbook(published, HashingEmbedder("fake-a"))
    embed_published_handbook(published, HashingEmbedder("fake-b"))

    assert _stored(published, "fake-a") == 133
    assert _stored(published, "fake-b") == 133


def test_requires_a_published_handbook(db_session: Session) -> None:
    with pytest.raises(NoPublishedHandbookError):
        embed_published_handbook(db_session, HashingEmbedder("fake-a"))
