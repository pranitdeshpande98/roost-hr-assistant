"""LLM judge for the two quality gates code cannot decide: faithfulness (every claim is
supported by the sources the agent had) and citation correctness (each [§x] supports the
statement it is attached to). Calibrated against hand-labelled examples before use."""

import json
import re
from collections.abc import Callable, Collection, Sequence
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from roost.evals.agent_harness import AgentCaseRecord, ToolCallRecord
from roost.llm.errors import LLMResponseError
from roost.llm.types import ChatMessage, ChatModel

JUDGE_PROMPT = """You check answers written by an HR assistant against the sources it was given.

SOURCES are excerpts from the English employee handbook and HR tool calls: the
arguments the assistant sent and what the tool returned. CONTEXT, when given, is what
the assistant was told about the conversation, such as today's date.
The ANSWER may be in English, Spanish or Vietnamese. Judge its meaning, not its wording.

Decide two things:
1. faithful: true if every factual claim in the ANSWER is supported by the SOURCES.
   Polite phrases, offers to help and advice to contact HR are not factual claims.
   A number, date, amount, condition or rule that the SOURCES do not state is NOT supported.
   A day of the week or a count of days correctly calculated from CONTEXT or a date in
   the SOURCES IS supported; a wrongly calculated one is not.
2. citations_correct: true if every citation like [§6.4] in the ANSWER points to a source
   section that supports the sentence it is attached to. True if there are no citations.

Reply with JSON only, no other text:
{"faithful": true|false, "unsupported_claims": ["..."],
 "citations_correct": true|false, "bad_citations": ["§x.y"], "reason": "one sentence"}"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    faithful: bool
    unsupported_claims: tuple[str, ...] = ()
    citations_correct: bool
    bad_citations: tuple[str, ...] = ()
    reason: str = ""


class Judgment(BaseModel):
    """One judged turn, stored as one JSONL line."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    turn: int  # 1-based
    judge: str  # model that judged
    verdict: Verdict | None = None
    error: str | None = None


def parse_verdict(text: str | None) -> Verdict:
    """The first JSON object in the reply (models sometimes add a sentence around it)."""
    match = _JSON_OBJECT.search(text or "")
    if match is None:
        raise LLMResponseError("judge reply has no JSON object")
    try:
        return Verdict.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError) as error:
        raise LLMResponseError(f"judge reply is not a valid verdict: {error}") from error


def judge_answer(
    model: ChatModel,
    answer: str,
    sources: Sequence[str],
    context: str = "",
    retries: int = 1,
) -> Verdict:
    """Ask the judge once (plus `retries` if its reply is malformed). Outages propagate."""
    content = f"CONTEXT:\n{context}\n\n" if context else ""
    content += "SOURCES:\n" + "\n\n---\n\n".join(sources) + f"\n\nANSWER:\n{answer}"
    messages = [
        ChatMessage(role="system", content=JUDGE_PROMPT),
        ChatMessage(role="user", content=content),
    ]
    for attempt in range(retries + 1):
        result = model.chat(messages, temperature=0.0, max_tokens=800)
        try:
            return parse_verdict(result.message.content)
        except LLMResponseError:
            if attempt == retries:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


# --- calibration: does the judge agree with a human? ---------------------------------


class CalibrationItem(BaseModel):
    """A hand-labelled example: the judge must reach the same verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    note: str  # what a human sees in it
    context: str = ""
    sources: tuple[str, ...] = Field(min_length=1)
    answer: str
    faithful: bool
    citations_correct: bool


def load_calibration(path: Path) -> list[CalibrationItem]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = [CalibrationItem.model_validate(item) for item in raw]
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate calibration ids")
    return items


class Agreement(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: int
    faithful_agreement: float
    citation_agreement: float
    caught_unfaithful: float  # of the answers a human marked unfaithful, share the judge caught
    disagreements: tuple[str, ...]


def agreement(items: Sequence[CalibrationItem], verdicts: Sequence[Verdict]) -> Agreement:
    pairs = list(zip(items, verdicts, strict=True))
    unfaithful = [(i, v) for i, v in pairs if not i.faithful]
    disagreements = [
        f"{i.id}: human faithful={i.faithful} citations={i.citations_correct}; "
        f"judge faithful={v.faithful} citations={v.citations_correct} ({v.reason})"
        for i, v in pairs
        if (i.faithful, i.citations_correct) != (v.faithful, v.citations_correct)
    ]
    return Agreement(
        items=len(pairs),
        faithful_agreement=sum(i.faithful == v.faithful for i, v in pairs) / len(pairs),
        citation_agreement=sum(i.citations_correct == v.citations_correct for i, v in pairs)
        / len(pairs),
        caught_unfaithful=(
            sum(not v.faithful for _, v in unfaithful) / len(unfaithful) if unfaithful else 1.0
        ),
        disagreements=tuple(disagreements),
    )


# --- judging recorded agent conversations ---------------------------------------------


def date_context(today: date) -> str:
    """The same date line the agent's system prompt gives it."""
    return f"Today is {today.isoformat()} ({today.strftime('%A')})."


def tool_source(call: ToolCallRecord) -> str:
    """A tool call as the judge sees it: what was asked and what came back. The
    arguments matter: a confirmation reply repeats the dates that were booked."""
    arguments = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
    return f"Tool {call.name} called with {arguments} returned:\n{call.result}"


def judgeable_turns(
    record: AgentCaseRecord, strip: Callable[[str], str]
) -> list[tuple[int, str, list[str]]]:
    """(turn number, answer, sources) for every turn worth judging: the model answered in
    its own words, the turn is not a confirmation question, and the conversation so far
    has tool results to judge against (questions with no sources are left to the
    deterministic checks)."""
    sources: list[str] = []
    turns = []
    for number, turn in enumerate(record.turns, start=1):
        for call in turn.tool_calls:
            source = tool_source(call)
            if call.result and source not in sources:
                sources.append(source)
        answer = strip(turn.reply)
        if answer and not turn.paused and sources:
            turns.append((number, answer, list(sources)))
    return turns


def judgments_path(out: Path, results_stem: str) -> Path:
    """Judgments live next to the results they judge, in a subfolder (not *.jsonl in out)."""
    return out / "judgments" / f"{results_stem}.jsonl"


def load_judgments(path: Path) -> list[Judgment]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Judgment.model_validate_json(line) for line in lines if line.strip()]


def remove_judgments(path: Path, case_ids: Collection[str] | None = None) -> int:
    """Drop judgments (of the given cases, or all) so the next run judges them again.
    Returns how many were removed."""
    judgments = load_judgments(path)
    kept = [j for j in judgments if case_ids is not None and j.case_id not in case_ids]
    if len(kept) != len(judgments):
        path.write_text(
            "".join(j.model_dump_json() + "\n" for j in kept), encoding="utf-8", newline="\n"
        )
    return len(judgments) - len(kept)


def append_judgment(path: Path, judgment: Judgment) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(judgment.model_dump_json() + "\n")
