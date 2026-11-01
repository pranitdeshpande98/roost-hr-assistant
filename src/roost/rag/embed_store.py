"""Embed the published handbook's chunks and store the vectors (chunk_embedding)."""

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from roost.core.enums import HandbookStatus
from roost.db.models import ChunkEmbedding, HandbookChunk, HandbookVersion
from roost.rag.embeddings import Embedder, chunk_text_for_embedding


class NoPublishedHandbookError(RuntimeError):
    """There is no published handbook version to embed."""


def embed_published_handbook(session: Session, embedder: Embedder, *, batch_size: int = 32) -> int:
    """Embed every published chunk that has no vector yet for this model.

    Safe to re-run: chunks already embedded with the same model are skipped.
    Returns the number of chunks embedded.
    """
    version_id = session.scalar(
        select(HandbookVersion.id).where(HandbookVersion.status == HandbookStatus.PUBLISHED)
    )
    if version_id is None:
        raise NoPublishedHandbookError("Publish a handbook version before embedding it.")

    already_embedded = select(ChunkEmbedding.chunk_id).where(
        ChunkEmbedding.model_name == embedder.model_name
    )
    chunks = session.execute(
        select(HandbookChunk.id, HandbookChunk.heading_path, HandbookChunk.content)
        .where(HandbookChunk.version_id == version_id, HandbookChunk.id.not_in(already_embedded))
        .order_by(HandbookChunk.chunk_index)
    ).all()

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = embedder.embed_documents(
            [chunk_text_for_embedding(c.heading_path, c.content) for c in batch]
        )
        session.execute(
            insert(ChunkEmbedding),
            [
                {"chunk_id": c.id, "model_name": embedder.model_name, "embedding": vector}
                for c, vector in zip(batch, vectors, strict=True)
            ],
        )
    return len(chunks)
