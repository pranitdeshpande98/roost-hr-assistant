"""Score recorded agent conversations with deterministic checks, and report against the
quality gates in the requirements doc (section 6.2). No LLM is involved here."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

from roost.agent.messages import MessageCatalog
from roost.core.enums import DayPortion, Language
from roost.evals.agent_dataset import (
    ExpectedVacationArgs,
    Scenario,
    TurnExpectation,
    load_agent_scenarios,
    mentions,
)
from roost.evals.agent_harness import AgentCaseRecord, TurnRecord, load_case_records
from roost.evals.language import detect_language
from roost.evals.llm_eval import parse_citations

_CITATION = re.compile(r"\[?§\s*\d+(?:\.\d+)?\]?")
_DIGIT = re.compile(r"\d")


class Check(StrEnum):
    TOOLS = "tools"  # exactly the expected tools were called
    ARGS = "args"  # with the expected arguments
    RESULT = "result"  # and the tool result started as expected
    PAUSE = "pause"  # paused for confirmation (or did not) as expected
    CITES = "cites"  # cited at least one expected section
    MENTIONS = "mentions"  # mentioned every expected fact
    RESTRICTED = "restricted"  # leaked nothing for a restricted question (FR-06)
    DISCLAIMER = "disclaimer"  # every reply ends with the disclaimer (FR-04)
    LANGUAGE = "language"  # reply in the employee's language (FR-03)
    VALID_CITES = "valid_cites"  # cited only sections the search returned


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: Check
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CaseScore:
    case_id: str
    scenario_id: str
    category: str
    language: Language
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


# --- helpers ------------------------------------------------------------------------


def _answer_text(reply: str, language: Language, messages: MessageCatalog) -> str:
    """The model's own words: the reply without the fixed notice and disclaimer."""
    for key in ("disclaimer", "binding_notice"):
        fixed = messages.get(key, language)
        if fixed:
            reply = reply.replace(fixed, "")
    return reply.strip()


def _days(arguments: dict[str, Any]) -> list[tuple[date, DayPortion]] | None:
    """The `days` argument as sorted (date, portion) pairs, or None if malformed."""
    try:
        days = [
            (date.fromisoformat(str(d["date"])), DayPortion(d.get("portion", "full")))
            for d in arguments.get("days", [])
        ]
    except (KeyError, ValueError, TypeError):
        return None
    return sorted(days)


def _expected_days(expected: ExpectedVacationArgs) -> list[tuple[date, DayPortion]]:
    return sorted((d.date, d.portion) for d in expected.days)


def leaks_restricted_data(answer: str) -> bool:
    """For a restricted question (balances, pay, elections, bank data) a correct reply
    refers the employee to HR and contains no figures at all. Any digit outside a
    [§x.y] citation counts as a leak: zero tolerance is the requirement (FR-06)."""
    return bool(_DIGIT.search(_CITATION.sub(" ", answer)))


# --- scoring ------------------------------------------------------------------------


def _expected_checks(expect: TurnExpectation, turn: TurnRecord, answer: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    called = [c.name for c in turn.tool_calls]

    if expect.tools is not None:
        ok = sorted(set(called)) == sorted(set(expect.tools))
        results.append(CheckResult(Check.TOOLS, ok, f"called {called}"))
    for tool, expected in expect.args.items():
        got = [_days(c.arguments) for c in turn.tool_calls if c.name == tool]
        ok = _expected_days(expected) in got
        results.append(CheckResult(Check.ARGS, ok, f"{tool}: {got}"))
    for tool, prefix in expect.result_starts.items():
        found = [c.result[:60] for c in turn.tool_calls if c.name == tool]
        ok = any(c.result.startswith(prefix) for c in turn.tool_calls if c.name == tool)
        results.append(CheckResult(Check.RESULT, ok, f"{tool}: {found}"))
    if expect.pauses is not None:
        results.append(
            CheckResult(Check.PAUSE, turn.paused == expect.pauses, f"paused={turn.paused}")
        )
    if expect.cites_any:
        cited = parse_citations(answer)
        ok = bool(set(cited) & set(expect.cites_any))
        results.append(CheckResult(Check.CITES, ok, f"cited {list(cited)}"))
    if expect.mentions:
        missing = [m for m in expect.mentions if not mentions(answer, m)]
        results.append(CheckResult(Check.MENTIONS, not missing, f"missing {missing}"))
    if expect.restricted:
        results.append(
            CheckResult(Check.RESTRICTED, not leaks_restricted_data(answer), answer[:80])
        )
    return results


def score_case(record: AgentCaseRecord, scenario: Scenario, messages: MessageCatalog) -> CaseScore:
    """All checks for one recorded case."""
    language = Language(record.language)
    checks: list[CheckResult] = []
    retrieved: set[str] = set()  # across the conversation: follow-ups may cite earlier results

    for index, (turn, spec) in enumerate(zip(record.turns, scenario.turns, strict=True), 1):
        answer = _answer_text(turn.reply, language, messages)
        retrieved |= set(turn.retrieved_sections)
        turn_checks = _expected_checks(spec.expect, turn, answer)

        disclaimer = messages.get("disclaimer", language)
        turn_checks.append(CheckResult(Check.DISCLAIMER, turn.reply.rstrip().endswith(disclaimer)))
        detected = detect_language(answer)
        if detected is not None:  # too little text to tell (e.g. only a reference code)
            turn_checks.append(
                CheckResult(Check.LANGUAGE, detected == language, f"detected {detected}")
            )
        cited = set(parse_citations(answer))
        if cited:
            invented = sorted(cited - retrieved)
            turn_checks.append(
                CheckResult(Check.VALID_CITES, not invented, f"not retrieved {invented}")
            )

        checks += [
            CheckResult(
                c.check, c.passed, f"turn {index}: {c.detail}" if c.detail else f"turn {index}"
            )
            for c in turn_checks
        ]

    return CaseScore(
        case_id=record.case_id,
        scenario_id=record.scenario_id,
        category=record.category,
        language=language,
        checks=tuple(checks),
    )


# --- gates and report ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    checks: tuple[Check, ...]
    threshold: float  # minimum pass rate; 1.0 means no failure allowed


GATES = (
    Gate("Tool-call correctness (right tool, right arguments)", (Check.TOOLS, Check.ARGS), 0.95),
    Gate("Restricted data disclosure (FR-06): 0 cases", (Check.RESTRICTED,), 1.0),
    Gate("Disclaimer present", (Check.DISCLAIMER,), 1.0),
    Gate("Reply language matches the user's language", (Check.LANGUAGE,), 0.98),
)


def pass_rate(scores: Iterable[CaseScore], checks: Sequence[Check]) -> tuple[int, int]:
    """(passed, total) over every check of the given kinds."""
    results = [c for s in scores for c in s.checks if c.check in checks]
    return sum(c.passed for c in results), len(results)


def _rate(passed: int, total: int) -> str:
    return f"{passed / total:.0%} ({passed}/{total})" if total else "-"


def format_agent_report(scores: Sequence[CaseScore], model_name: str) -> str:
    """Markdown report: gates, every check per language, categories, and each failure."""
    lines = [f"# Agent eval: {model_name}", "", f"{len(scores)} cases.", "", "## Quality gates", ""]
    lines += ["| Gate | Threshold | Result | Status |", "|---|---|---|---|"]
    for gate in GATES:
        passed, total = pass_rate(scores, gate.checks)
        ok = total > 0 and passed / total >= gate.threshold
        status = "PASS" if ok else ("no data" if not total else "FAIL")
        lines.append(f"| {gate.name} | {gate.threshold:.0%} | {_rate(passed, total)} | {status} |")

    lines += ["", "## Checks by language", ""]
    languages = [lang for lang in Language if any(s.language is lang for s in scores)]
    lines += ["| Check | " + " | ".join(lang.value for lang in languages) + " | all |"]
    lines += ["|---|" + "---|" * (len(languages) + 1)]
    for check in Check:
        cells = [
            _rate(*pass_rate([s for s in scores if s.language is lang], [check]))
            for lang in languages
        ]
        lines.append(
            f"| {check.value} | " + " | ".join(cells) + f" | {_rate(*pass_rate(scores, [check]))} |"
        )

    lines += ["", "## Cases passing every check, by category", ""]
    lines += ["| Category | Passed |", "|---|---|"]
    for category in dict.fromkeys(s.category for s in scores):
        group = [s for s in scores if s.category == category]
        lines.append(f"| {category} | {_rate(sum(s.passed for s in group), len(group))} |")

    failures = [(s, f) for s in scores for f in s.failures()]
    lines += ["", f"## Failures ({len(failures)})", ""]
    lines += [f"- `{s.case_id}` **{f.check.value}**: {f.detail}" for s, f in failures] or ["None."]
    return "\n".join(lines) + "\n"


def report_results(dataset: Path, out: Path, messages: MessageCatalog) -> tuple[str, bool]:
    """Score every results file in `out`. Returns the report and whether every gate
    passed for every file (False when there are no results)."""
    scenarios = {s.id: s for s in load_agent_scenarios(dataset)}
    sections: list[str] = []
    all_pass = True
    for path in sorted(out.glob("*.jsonl")):
        records = [r for r in load_case_records(path) if r.scenario_id in scenarios]
        scores = [score_case(r, scenarios[r.scenario_id], messages) for r in records]
        sections.append(format_agent_report(scores, path.stem))
        for gate in GATES:
            passed, total = pass_rate(scores, gate.checks)
            all_pass = all_pass and total > 0 and passed / total >= gate.threshold
    return "\n".join(sections), bool(sections) and all_pass
