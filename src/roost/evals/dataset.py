"""Load and validate evaluation datasets."""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from roost.core.enums import Language

SectionNumber = Annotated[str, StringConstraints(pattern=r"^\d+(\.\d+)?$")]
IntentId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]


class RetrievalIntent(BaseModel):
    """One question asked in every supported language (one entry in the YAML file)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: IntentId
    expected_sections: tuple[SectionNumber, ...] = Field(min_length=1)
    questions: dict[Language, str]

    @field_validator("questions")
    @classmethod
    def _every_language_present(cls, questions: dict[Language, str]) -> dict[Language, str]:
        missing = set(Language) - set(questions)
        if missing:
            raise ValueError(f"missing languages: {', '.join(sorted(missing))}")
        if any(not text.strip() for text in questions.values()):
            raise ValueError("questions must not be empty")
        return questions


class RetrievalCase(BaseModel):
    """One question in one language, with the sections that answer it."""

    model_config = ConfigDict(frozen=True)

    case_id: str  # e.g. "vacation-carryover-vi"
    intent: str
    language: Language
    question: str
    expected_sections: tuple[str, ...]


def load_retrieval_cases(path: Path) -> list[RetrievalCase]:
    """Read a retrieval dataset and expand each intent into one case per language."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: expected a list of intents")

    intents = [RetrievalIntent.model_validate(item) for item in raw]
    ids = [intent.id for intent in intents]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path.name}: duplicate intent ids: {', '.join(duplicates)}")

    return [
        RetrievalCase(
            case_id=f"{intent.id}-{language.value}",
            intent=intent.id,
            language=language,
            question=intent.questions[language],
            expected_sections=intent.expected_sections,
        )
        for intent in intents
        for language in Language
    ]
