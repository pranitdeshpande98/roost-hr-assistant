"""Tests for roost.evals.metrics."""

import pytest

from roost.evals.metrics import first_relevant_rank, hit_at_k, reciprocal_rank

RANKED = ["6.4", "7.1", "9.2", "6.9"]


@pytest.mark.parametrize(
    ("expected", "rank"),
    [
        (["6.4"], 1),
        (["9.2"], 3),
        (["6.9", "7.1"], 2),  # any expected section counts; the best one wins
        (["12.2"], None),
    ],
)
def test_first_relevant_rank(expected: list[str], rank: int | None) -> None:
    assert first_relevant_rank(RANKED, expected) == rank


def test_first_relevant_rank_of_empty_ranking_is_none() -> None:
    assert first_relevant_rank([], ["6.4"]) is None


@pytest.mark.parametrize(("k", "hit"), [(2, False), (3, True), (5, True)])
def test_hit_at_k_respects_the_cutoff(k: int, hit: bool) -> None:
    assert hit_at_k(RANKED, ["9.2"], k) is hit


def test_hit_at_k_is_false_when_missing() -> None:
    assert hit_at_k(RANKED, ["12.2"], 10) is False


@pytest.mark.parametrize(
    ("expected", "score"),
    [(["6.4"], 1.0), (["7.1"], 0.5), (["6.9"], 0.25), (["12.2"], 0.0)],
)
def test_reciprocal_rank(expected: list[str], score: float) -> None:
    assert reciprocal_rank(RANKED, expected) == pytest.approx(score)
