"""Load and validate the agent evaluation set (evals/datasets/agent_v1.yaml)."""

import re
from datetime import date
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roost.core.enums import DayPortion, Language
from roost.guardrails.types import Route

# The harness pins the agent's clock here, so relative dates in the questions are stable.
EVAL_TODAY = date(2026, 11, 2)  # a Monday

# Every tool the agent offers. A test checks this against the real agent's registry.
KNOWN_TOOLS = frozenset(
    {
        "search_handbook",
        "request_vacation",
        "change_vacation_request",
        "cancel_vacation_request",
        "list_my_vacation_requests",
        "my_pay_schedule",
    }
)

Category = Literal[
    "handbook",
    "payday",
    "vacation",
    "restricted",
    "out_of_scope",
    "multi_turn",
    "sensitive",  # sensitive matters and emergencies: a fixed reply (ADR-0012)
    "injection",
]
_CITATION = re.compile(r"§\s*\d+(?:\.\d+)?")
_NUMBER = re.compile(r"\d+")


class EmployeeProfile(StrEnum):
    """Which kind of seeded employee a scenario signs in as; the harness picks one."""

    OFFICE_BIWEEKLY = "office_biweekly"  # corporate office, Mon-Fri, paid every other Friday
    PRODUCTION_WEEKLY = "production_weekly"  # plant floor, paid every Friday


class Utterance(BaseModel):
    """What the employee says, in every language."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    en: str = Field(min_length=1)
    es: str = Field(min_length=1)
    vi: str = Field(min_length=1)

    def in_language(self, language: Language) -> str:
        return str(getattr(self, language.value))


class ExpectedDay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    date: date
    portion: DayPortion


class ExpectedVacationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    days: tuple[ExpectedDay, ...] = Field(min_length=1)


class TurnExpectation(BaseModel):
    """What must hold after one turn. Anything left unset is not checked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: tuple[str, ...] | None = None
    args: dict[str, ExpectedVacationArgs] = Field(default_factory=dict)
    result_starts: dict[str, str] = Field(default_factory=dict)
    pauses: bool | None = None
    cites_any: tuple[str, ...] = ()
    mentions: tuple[str, ...] = ()
    restricted: bool = False
    route: Route | None = None  # where the input guardrail must send the message

    @model_validator(mode="after")
    def _known_tools(self) -> "TurnExpectation":
        named = set(self.tools or ()) | set(self.args) | set(self.result_starts)
        unknown = named - KNOWN_TOOLS
        if unknown:
            raise ValueError(f"unknown tools: {', '.join(sorted(unknown))}")
        return self


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    say: Utterance
    expect: TurnExpectation = TurnExpectation()


class Scenario(BaseModel):
    """One conversation, run once in each language."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    category: Category
    employee: EmployeeProfile
    turns: tuple[Turn, ...] = Field(min_length=1, max_length=6)

    @field_validator("turns")
    @classmethod
    def _only_the_last_turn_may_be_left_paused(cls, turns: tuple[Turn, ...]) -> tuple[Turn, ...]:
        # A paused turn must be followed by the employee's answer, or be the last turn.
        for turn, following in pairwise(turns):
            if turn.expect.pauses and following.expect.pauses:
                raise ValueError("two paused turns in a row: the second would be an answer")
        return turns


class AgentCase(BaseModel):
    """One scenario in one language: what the harness actually runs."""

    model_config = ConfigDict(frozen=True)

    case_id: str  # e.g. "payday-weekly-vi"
    scenario: Scenario
    language: Language

    @property
    def messages(self) -> list[str]:
        return [turn.say.in_language(self.language) for turn in self.scenario.turns]


def load_agent_scenarios(path: Path) -> list[Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: expected a list of scenarios")
    scenarios = [Scenario.model_validate(item) for item in raw]
    ids = [s.id for s in scenarios]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path.name}: duplicate scenario ids: {', '.join(duplicates)}")
    return scenarios


def expand_cases(scenarios: list[Scenario]) -> list[AgentCase]:
    """Every scenario in every language."""
    return [
        AgentCase(case_id=f"{s.id}-{language.value}", scenario=s, language=language)
        for s in scenarios
        for language in Language
    ]


# Models often write typographic dashes and spaces ("VR\u2011C82K9X", "14\u202fdays"): compare
# them as the plain ASCII characters a reader sees.
_LOOKALIKES = str.maketrans(
    {
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2212": "-",  # minus sign
        "\u00a0": " ",  # no-break space
        "\u202f": " ",  # narrow no-break space
    }
)


def normalize_text(text: str) -> str:
    """Typographic dashes and spaces replaced by their ASCII look-alikes."""
    return text.translate(_LOOKALIKES)


def mentions(reply: str, expected: str) -> bool:
    """Whether `reply` mentions `expected`.

    A number is matched as a whole number after removing [§x.y] citations, so "6" matches
    "6" or "06" but not "2026", and "2" is not satisfied by a citation like [§9.2].
    Anything else is a case-insensitive substring. Typographic dashes and spaces count
    as their ASCII look-alikes.
    """
    reply = normalize_text(reply)
    if expected.isdigit():
        numbers = _NUMBER.findall(_CITATION.sub(" ", reply))
        return any(int(found) == int(expected) for found in numbers)
    return expected.lower() in reply.lower()
