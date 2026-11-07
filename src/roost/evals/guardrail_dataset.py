"""Load and validate the guardrail evaluation set (evals/datasets/guardrails_v1.yaml).

Each item is one message, written in every language, labelled with the category the
input guardrail must assign. Items are split into dev (may be used to tune thresholds)
and test (only for the reported score), so tuning cannot fit the reported number.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from roost.core.enums import Language
from roost.evals.agent_dataset import Utterance
from roost.guardrails.types import ROUTE_OF, Category, Route

Split = Literal["dev", "test"]
# Why an allowed message is hard: which other route it resembles (see the dataset header).
Tag = Literal["procedure", "policy", "tool", "handbook", "small_talk"]


class GuardrailItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    category: Category
    split: Split
    tag: Tag | None = None
    say: Utterance

    @model_validator(mode="after")
    def _tag_only_on_allowed(self) -> "GuardrailItem":
        allowed = self.category is Category.NONE
        if allowed and self.tag is None:
            raise ValueError(f"{self.id}: an allowed message needs a tag")
        if not allowed and self.tag is not None:
            raise ValueError(f"{self.id}: only allowed messages have a tag")
        return self

    @property
    def route(self) -> Route:
        return ROUTE_OF[self.category]


@dataclass(frozen=True, slots=True)
class GuardrailCase:
    """One item in one language: what the classifier is run on."""

    item: GuardrailItem
    language: Language

    @property
    def case_id(self) -> str:
        return f"{self.item.id}-{self.language.value}"

    @property
    def text(self) -> str:
        return self.item.say.in_language(self.language)


def load_guardrail_items(path: Path) -> list[GuardrailItem]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = [GuardrailItem.model_validate(entry) for entry in raw]
    ids = [i.id for i in items]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate guardrail ids: {', '.join(duplicates)}")
    return items


def expand_guardrail_cases(
    items: Sequence[GuardrailItem], split: Split | None = None
) -> list[GuardrailCase]:
    """Every item in every language, optionally only one split."""
    return [
        GuardrailCase(item, language)
        for item in items
        if split is None or item.split == split
        for language in Language
    ]
