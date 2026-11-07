"""Tests for the guardrail evaluation set and its loader."""

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from roost.core.enums import Language
from roost.evals.guardrail_dataset import (
    GuardrailItem,
    expand_guardrail_cases,
    load_guardrail_items,
)
from roost.evals.language import detect_language
from roost.guardrails.types import ROUTE_OF, Category, Route

DATASETS = Path(__file__).resolve().parents[3] / "evals" / "datasets"
DATASET = DATASETS / "guardrails_v2.yaml"


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "x",
        "category": "harassment",
        "split": "dev",
        "say": {"en": "a", "es": "b", "vi": "c"},
    }
    return item | overrides


def test_every_category_has_a_route() -> None:
    assert set(ROUTE_OF) == set(Category)


def test_real_dataset_covers_every_category_in_both_splits() -> None:
    items = load_guardrail_items(DATASET)

    for split in ("dev", "test"):
        covered = {i.category for i in items if i.split == split}
        assert covered == set(Category), f"{split} misses {set(Category) - covered}"


def test_real_dataset_has_hard_negatives_for_each_risky_route() -> None:
    tags = Counter(i.tag for i in load_guardrail_items(DATASET) if i.route is Route.ALLOWED)

    assert tags["procedure"] >= 10  # "how do I report an injury?" is not a disclosure
    assert tags["policy"] >= 6  # "how many vacation days do I get?" is not own data
    assert tags["tool"] >= 6  # "when is my payday?" is answered by a tool


def test_real_dataset_size() -> None:
    items = load_guardrail_items(DATASET)
    routes = Counter(i.route for i in items)

    assert len(items) == 162
    assert len(expand_guardrail_cases(items)) == 486
    assert sum(i.split == "test" for i in items) == 60  # the holdout
    assert routes[Route.EMERGENCY] == 12
    assert routes[Route.SENSITIVE] == 44
    assert routes[Route.ALLOWED] == 66


def test_v2_development_half_is_all_of_v1() -> None:
    v1 = load_guardrail_items(DATASETS / "guardrails_v1.yaml")
    v2_dev = [i for i in load_guardrail_items(DATASET) if i.split == "dev"]

    assert [(i.id, i.category, i.say) for i in v1] == [(i.id, i.category, i.say) for i in v2_dev]


def test_messages_are_in_their_language() -> None:
    for case in expand_guardrail_cases(load_guardrail_items(DATASET)):
        detected = detect_language(case.text)
        if case.language is Language.VI:
            assert detected is Language.VI, case.case_id
        else:  # short messages may have too few clues; they must not look like another
            assert detected in (case.language, None), case.case_id


def test_expand_filters_by_split() -> None:
    items = load_guardrail_items(DATASET)

    dev = expand_guardrail_cases(items, "dev")
    test = expand_guardrail_cases(items, "test")

    assert len(dev) + len(test) == 486
    assert {c.item.split for c in dev} == {"dev"}
    assert dev[0].case_id.endswith("-en")


def test_an_allowed_message_needs_a_tag() -> None:
    with pytest.raises(ValidationError, match="needs a tag"):
        GuardrailItem.model_validate(_item(category="none"))


def test_only_allowed_messages_have_a_tag() -> None:
    with pytest.raises(ValidationError, match="only allowed"):
        GuardrailItem.model_validate(_item(tag="procedure"))


def test_a_missing_language_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GuardrailItem.model_validate(_item(say={"en": "a", "es": "b"}))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "g.yaml"
    path.write_text(yaml.safe_dump([_item(), _item()]), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate guardrail ids: x"):
        load_guardrail_items(path)
