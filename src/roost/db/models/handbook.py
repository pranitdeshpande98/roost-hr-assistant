"""Handbook knowledge tables: versions, section chunks and their embeddings."""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from roost.core.enums import HandbookStatus
from roost.db.base import Base
from roost.db.types import str_enum


class HandbookVersion(Base):
    """One published (or draft, or retired) version of the employee handbook."""

    __tablename__ = "handbook_version"
    __table_args__ = (
        # At most one version can be published at any time (FR-44).
        Index(
            "uq_handbook_version_single_published",
            "status",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    version_label: Mapped[str] = mapped_column(String(20), unique=True)
    effective_date: Mapped[date] = mapped_column(Date)
    status: Mapped[HandbookStatus] = mapped_column(
        str_enum(HandbookStatus),
        default=HandbookStatus.DRAFT,
        server_default=HandbookStatus.DRAFT.value,
    )
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HandbookChunk(Base):
    """One handbook section or subsection, the unit retrieved and cited (e.g. 6.4)."""

    __tablename__ = "handbook_chunk"
    __table_args__ = (
        UniqueConstraint("version_id", "section_number"),
        Index("ix_handbook_chunk_search_tsv", "search_tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("handbook_version.id", ondelete="CASCADE"), index=True
    )
    section_number: Mapped[str] = mapped_column(String(10))
    section_title: Mapped[str] = mapped_column(String(200))
    heading_path: Mapped[str] = mapped_column(String(400))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    # Kept in sync by Postgres for keyword search (ADR-0005 hybrid retrieval).
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', heading_path || ' ' || content)", persisted=True),
    )


class ChunkEmbedding(Base):
    """A chunk's vector from one embedding model; several models can coexist (ADR-0005)."""

    __tablename__ = "chunk_embedding"

    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("handbook_chunk.id", ondelete="CASCADE"), primary_key=True
    )
    model_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    # No fixed dimension: each model gets its own index when it is selected (Phase 4).
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
