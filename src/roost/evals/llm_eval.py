"""LLM eval: does a model search, answer in the user's language, and cite what it found?

One case = one question run through a small tool loop:
    model -> search_handbook(query) -> retrieved sections -> model -> final answer.
Records are saved one per line (JSONL) so long, rate-limited runs can stop and resume.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from roost.core.enums import Language
from roost.evals.dataset import RetrievalCase
from roost.evals.language import detect_language
from roost.llm.errors import LLMResponseError
from roost.llm.types import ChatMessage, ChatModel, ToolSpec
from roost.rag.retrieval import SectionText

SYSTEM_PROMPT = """You are Roost, the HR assistant for a poultry company's employees.
Rules:
1. Always call search_handbook before answering a policy question.
2. Answer only from the handbook sections the tool returns. If they do not answer the
   question, say so and tell the employee to contact their plant HR office.
3. Cite every section you use as [§<number>], for example [§6.4].
4. Reply in the same language as the employee's question (English, Spanish or
   Vietnamese), even though the handbook is in English.
5. Keep answers short and plain: a few sentences an hourly employee can read on a kiosk."""

SEARCH_TOOL = ToolSpec(
    name="search_handbook",
    description=(
        "Search the employee handbook. Returns the most relevant sections with their "
        "section numbers. Write the query in English for best results."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to look up."}},
        "required": ["query"],
    },
)

Search = Callable[[str], list[SectionText]]
_CITATION = re.compile(r"§\s*(\d+(?:\.\d+)?)")


class LLMCaseRecord(BaseModel):
    """Everything that happened for one case: stored as one JSONL line."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    intent: str
    language: Language
    expected_sections: tuple[str, ...]
    model: str
    tool_called_first: bool = False
    tool_queries: tuple[str, ...] = ()
    retrieved_sections: tuple[str, ...] = ()
    answer: str | None = None
    error: str | None = None
    turns: int = 0
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMCaseScore(BaseModel):
    """Pass/fail checks derived from a record."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    model: str
    language: Language
    errored: bool
    used_tool: bool  # searched before answering
    language_match: bool  # answered in the question's language
    cites_retrieved: bool  # cited at least one section, and only sections it was given
    cites_expected: bool  # at least one citation is a correct answer section
    latency_ms: float  # total model time for the case (all turns)
    total_tokens: int


def format_sections(sections: Sequence[SectionText]) -> str:
    """Tool result text shown to the model."""
    if not sections:
        return "No matching handbook sections."
    return "\n\n".join(f"[§{s.section_number}] {s.heading_path}\n{s.content}" for s in sections)


def parse_citations(text: str | None) -> tuple[str, ...]:
    """Section numbers cited as [§x.y] (or §x.y), in order, without duplicates."""
    if not text:
        return ()
    return tuple(dict.fromkeys(_CITATION.findall(text)))


def run_case(
    model: ChatModel,
    case: RetrievalCase,
    search: Search,
    *,
    max_turns: int = 3,
    max_tokens: int = 800,
) -> LLMCaseRecord:
    """Run one question through the tool loop. Model response errors become the record's
    `error`; unavailability (rate limit, outage) propagates so the runner can stop."""
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=case.question),
    ]
    queries: list[str] = []
    retrieved: list[str] = []
    tool_called_first = False
    latency = 0.0
    prompt_tokens = completion_tokens = 0
    answer: str | None = None
    error: str | None = None
    turns = 0

    try:
        for turn in range(max_turns):
            turns = turn + 1
            # Offer the tool until it has been used once; then ask for the answer.
            tools = [SEARCH_TOOL] if not queries else None
            result = model.chat(messages, tools=tools, max_tokens=max_tokens)
            latency += result.latency_ms
            prompt_tokens += result.usage.prompt_tokens
            completion_tokens += result.usage.completion_tokens
            calls = [c for c in result.message.tool_calls if c.name == SEARCH_TOOL.name]
            if turn == 0:
                tool_called_first = bool(calls)
            if not calls:
                answer = result.message.content
                break
            messages.append(result.message)
            for call in calls:
                query = str(call.arguments.get("query", case.question))
                sections = search(query)
                queries.append(query)
                retrieved.extend(s.section_number for s in sections)
                messages.append(
                    ChatMessage(
                        role="tool", content=format_sections(sections), tool_call_id=call.id
                    )
                )
        else:
            error = f"no final answer after {max_turns} turns"
    except LLMResponseError as exc:
        error = str(exc)

    return LLMCaseRecord(
        case_id=case.case_id,
        intent=case.intent,
        language=case.language,
        expected_sections=case.expected_sections,
        model=model.name,
        tool_called_first=tool_called_first,
        tool_queries=tuple(queries),
        retrieved_sections=tuple(dict.fromkeys(retrieved)),
        answer=answer,
        error=error,
        turns=turns,
        latency_ms=latency,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def score_record(record: LLMCaseRecord) -> LLMCaseScore:
    """Turn a record into pass/fail checks."""
    cited = parse_citations(record.answer)
    errored = record.error is not None or not record.answer
    return LLMCaseScore(
        case_id=record.case_id,
        model=record.model,
        language=record.language,
        errored=errored,
        used_tool=record.tool_called_first,
        language_match=not errored and detect_language(record.answer or "") == record.language,
        cites_retrieved=bool(cited) and set(cited) <= set(record.retrieved_sections),
        cites_expected=bool(set(cited) & set(record.expected_sections)),
        latency_ms=record.latency_ms,
        total_tokens=record.prompt_tokens + record.completion_tokens,
    )


# --- storage: one JSON object per line, appended as each case finishes ---------------


def load_records(path: Path) -> list[LLMCaseRecord]:
    """Records saved so far (empty if the file does not exist yet)."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [LLMCaseRecord.model_validate_json(line) for line in lines if line.strip()]


def append_record(path: Path, record: LLMCaseRecord) -> None:
    """Save one record immediately, so an interrupted run loses at most one case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": LF on every OS, so Windows runs don't trip the line-ending hook.
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(record.model_dump_json() + "\n")


# --- reporting ---------------------------------------------------------------------

_CHECKS = ("used_tool", "language_match", "cites_retrieved", "cites_expected")


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile; simple and exact for the small samples we have."""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
    return ordered[index]


def format_llm_report(scores: Iterable[LLMCaseScore]) -> str:
    """Markdown table: one row per model, % passing each check, then speed and tokens.

    Speed and tokens use only the cases that produced an answer, so failures that return
    instantly do not make a model look fast.
    """
    by_model: dict[str, list[LLMCaseScore]] = {}
    for score in scores:
        by_model.setdefault(score.model, []).append(score)

    header = [
        "model",
        "cases",
        "errors",
        "searched",
        "right language",
        "valid cites",
        "correct section",
        "median s",
        "p90 s",
        "tokens/case",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for model, rows in by_model.items():
        total = len(rows)
        cells = [model, str(total), f"{sum(r.errored for r in rows) / total:.0%}"]
        cells += [f"{sum(getattr(r, check) for r in rows) / total:.0%}" for check in _CHECKS]
        answered = [r for r in rows if not r.errored]
        if answered:
            seconds = [r.latency_ms / 1000 for r in answered]
            tokens = sum(r.total_tokens for r in answered) / len(answered)
            cells += [
                f"{_percentile(seconds, 0.5):.1f}",
                f"{_percentile(seconds, 0.9):.1f}",
                f"{tokens:.0f}",
            ]
        else:
            cells += ["-", "-", "-"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
