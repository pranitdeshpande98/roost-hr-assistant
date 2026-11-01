"""Tests for reciprocal rank fusion in roost.rag.retrieval."""

import pytest

from roost.rag.retrieval import reciprocal_rank_fusion


def test_item_ranked_high_in_both_lists_wins() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]])

    assert fused[:2] == ["a", "b"]  # tie on score, broken alphabetically
    assert set(fused) == {"a", "b", "c", "d"}


def test_agreement_beats_a_single_first_place() -> None:
    # "x" is 1st in one list only; "y" is 2nd in both, so it collects two scores.
    fused = reciprocal_rank_fusion([["x", "y"], ["z", "y"]])

    assert fused[0] == "y"


def test_scores_follow_the_formula() -> None:
    fused = reciprocal_rank_fusion([["a"], ["b", "a"]], k=1)

    # a: 1/(1+1) + 1/(1+2) = 0.833; b: 1/(1+1) = 0.5
    assert fused == ["a", "b"]


def test_empty_rankings_give_empty_result() -> None:
    assert reciprocal_rank_fusion([[], []]) == []


@pytest.mark.parametrize("ranking", [["a", "b", "c"], ["c"]])
def test_single_ranking_is_unchanged(ranking: list[str]) -> None:
    assert reciprocal_rank_fusion([ranking]) == ranking
