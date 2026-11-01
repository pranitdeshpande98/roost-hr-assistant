"""Retrieval metrics. A case counts as found if ANY of its expected sections is retrieved."""

from collections.abc import Sequence


def first_relevant_rank(ranked: Sequence[str], expected: Sequence[str]) -> int | None:
    """1-based position of the first expected section in the ranking, or None if absent."""
    wanted = set(expected)
    for position, section in enumerate(ranked, start=1):
        if section in wanted:
            return position
    return None


def hit_at_k(ranked: Sequence[str], expected: Sequence[str], k: int) -> bool:
    """True if an expected section appears in the top k results."""
    rank = first_relevant_rank(ranked, expected)
    return rank is not None and rank <= k


def reciprocal_rank(ranked: Sequence[str], expected: Sequence[str]) -> float:
    """1 / rank of the first expected section (1.0 = first place, 0.0 = not found)."""
    rank = first_relevant_rank(ranked, expected)
    return 0.0 if rank is None else 1.0 / rank
