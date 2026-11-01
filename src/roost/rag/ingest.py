"""Store a handbook version and its sections, and publish it (FR-44)."""

import hashlib
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from roost.core.enums import HandbookStatus
from roost.db.models import HandbookChunk, HandbookVersion
from roost.rag.parsing import parse_handbook


class HandbookAlreadyIngestedError(ValueError):
    """This version label or this exact file content is already stored."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What ingest_handbook() stored."""

    version_id: int
    version_label: str
    chunk_count: int
    published: bool


def sha256_hex(text: str) -> str:
    """Fingerprint of the handbook content, used to detect re-ingesting the same file."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_handbook(
    session: Session,
    *,
    markdown: str,
    source_filename: str,
    version_label: str,
    effective_date: date,
    publish: bool,
) -> IngestResult:
    """Parse the handbook and store it as a new version, inside the caller's transaction.

    Versions are immutable: re-using a label or re-ingesting identical content
    is refused. With publish=True the new version replaces the published one.
    """
    fingerprint = sha256_hex(markdown)
    existing = session.scalar(
        select(HandbookVersion.version_label).where(
            (HandbookVersion.version_label == version_label)
            | (HandbookVersion.source_sha256 == fingerprint)
        )
    )
    if existing is not None:
        raise HandbookAlreadyIngestedError(
            f"Handbook already ingested as version {existing} (same label or same content)."
        )

    sections = parse_handbook(markdown)  # fails before anything is written
    version = HandbookVersion(
        version_label=version_label,
        effective_date=effective_date,
        source_filename=source_filename,
        source_sha256=fingerprint,
    )
    session.add(version)
    session.flush()  # assigns version.id

    session.add_all(
        HandbookChunk(
            version_id=version.id,
            section_number=section.number,
            section_title=section.title,
            heading_path=section.heading_path,
            chunk_index=index,
            content=section.content,
        )
        for index, section in enumerate(sections)
    )
    session.flush()

    if publish:
        publish_version(session, version.id)
    return IngestResult(version.id, version_label, len(sections), publish)


def publish_version(session: Session, version_id: int) -> None:
    """Retire the currently published version and publish this one, atomically."""
    session.execute(
        update(HandbookVersion)
        .where(HandbookVersion.status == HandbookStatus.PUBLISHED)
        .values(status=HandbookStatus.RETIRED)
    )
    session.execute(
        update(HandbookVersion)
        .where(HandbookVersion.id == version_id)
        .values(status=HandbookStatus.PUBLISHED, published_at=func.now())
    )
