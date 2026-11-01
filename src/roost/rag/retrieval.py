"""Search the published handbook: keyword, vector and hybrid (ADR-0005)."""

from collections.abc import Sequence

from sqlalchemy import ScalarSelect, Text, cast, func, select
from sqlalchemy.orm import Session

from roost.core.enums import HandbookStatus
from roost.db.models import ChunkEmbedding, HandbookChunk, HandbookVersion

RRF_K = 60  # standard reciprocal-rank-fusion constant; dampens the weight of top ranks


def _published_version_id() -> ScalarSelect[int]:
    """Subquery for the id of the single published handbook version."""
    return (
        select(HandbookVersion.id)
        .where(HandbookVersion.status == HandbookStatus.PUBLISHED)
        .scalar_subquery()
    )


def keyword_search(session: Session, query: str, *, limit: int) -> list[str]:
    """Section numbers ranked by Postgres full-text relevance (English stemming).

    Terms are OR-ed: a question rarely contains every word of the section that answers
    it, and plain AND matching would return almost nothing. ts_rank still puts sections
    matching more terms first.
    """
    any_term = func.replace(cast(func.plainto_tsquery("english", query), Text), " & ", " | ")
    tsquery = func.to_tsquery("english", any_term)
    rows = session.scalars(
        select(HandbookChunk.section_number)
        .where(
            HandbookChunk.version_id == _published_version_id(),
            HandbookChunk.search_tsv.op("@@")(tsquery),
        )
        .order_by(func.ts_rank(HandbookChunk.search_tsv, tsquery).desc())
        .limit(limit)
    )
    return list(rows)


def vector_search(
    session: Session, model_name: str, query_vector: Sequence[float], *, limit: int
) -> list[str]:
    """Section numbers ranked by cosine similarity to the query vector."""
    distance = ChunkEmbedding.embedding.cosine_distance(list(query_vector))
    rows = session.scalars(
        select(HandbookChunk.section_number)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == HandbookChunk.id)
        .where(
            HandbookChunk.version_id == _published_version_id(),
            ChunkEmbedding.model_name == model_name,
        )
        .order_by(distance)
        .limit(limit)
    )
    return list(rows)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, k: int = RRF_K) -> list[str]:
    """Merge several rankings: each item scores the sum of 1 / (k + rank) across them."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))


def hybrid_search(
    session: Session,
    model_name: str,
    query: str,
    query_vector: Sequence[float],
    *,
    limit: int,
    candidates: int = 20,
) -> list[str]:
    """Keyword and vector results fused with reciprocal rank fusion."""
    keyword = keyword_search(session, query, limit=candidates)
    vector = vector_search(session, model_name, query_vector, limit=candidates)
    return reciprocal_rank_fusion([keyword, vector])[:limit]
