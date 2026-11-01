"""Run eval cases through the real agent and record what happened, turn by turn.

Each case runs inside a database transaction that is rolled back afterwards, so cases
can book real vacation requests without leaving anything behind or affecting each other.
"""

import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, exists, select
from sqlalchemy.orm import Session

from roost.agent.conversation import ask
from roost.agent.state import AgentState
from roost.core.enums import (
    Channel,
    EmploymentStatus,
    LeaveType,
    PayrollClass,
    VacationRequestStatus,
)
from roost.db.models import Employee, LeaveBalance, Shift, VacationRequest
from roost.evals.agent_dataset import AgentCase, EmployeeProfile
from roost.evals.llm_eval import parse_citations
from roost.llm.errors import LLMUnavailableError
from roost.llm.types import ChatMessage, ChatModel, ChatResult, ToolSpec

Sessions = Callable[[], AbstractContextManager[Session]]
Agent = CompiledStateGraph[AgentState, Any, Any, Any]
MakeAgent = Callable[[Sessions], Agent]

# Evals run as the kiosk: the stricter channel (shortest answers, plain sentences).
EVAL_CHANNEL = Channel.KIOSK

# --- records ------------------------------------------------------------------------


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    arguments: dict[str, Any]
    result: str


class TurnRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    say: str
    reply: str
    paused: bool
    tool_calls: tuple[ToolCallRecord, ...] = ()
    retrieved_sections: tuple[str, ...] = ()  # sections returned by search_handbook
    models: tuple[str, ...] = ()  # which model answered each call (shows fallbacks)


class AgentCaseRecord(BaseModel):
    """One case's full transcript: one JSONL line."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    scenario_id: str
    category: str
    language: str
    turns: tuple[TurnRecord, ...]
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int


# --- the model wrapper ----------------------------------------------------------------


class ModelOutageError(RuntimeError):
    """Every model in the chain was unavailable: stop the run and resume later."""


class RecordingChatModel:
    """Wraps the real chain and records each call. The production graph turns an outage
    into a polite fallback reply; an eval must notice it instead of scoring that reply."""

    def __init__(self, inner: ChatModel) -> None:
        self._inner = inner
        self.results: list[ChatResult] = []
        self.outages: list[LLMUnavailableError] = []

    @property
    def name(self) -> str:
        return self._inner.name

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        try:
            result = self._inner.chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            )
        except LLMUnavailableError as error:
            self.outages.append(error)
            raise
        self.results.append(result)
        return result

    def take(self) -> list[ChatResult]:
        """The calls since the last take(), and fail if any of them was an outage."""
        if self.outages:
            error = self.outages[-1]
            self.outages.clear()
            raise ModelOutageError(str(error)) from error
        taken, self.results = self.results, []
        return taken


# --- database isolation and employees -----------------------------------------------


@contextmanager
def rolled_back_sessions(engine: Engine) -> Iterator[Sessions]:
    """Sessions that share one connection whose transaction is always rolled back.

    Tools call session.commit(); with join_transaction_mode="create_savepoint" that only
    releases a savepoint inside our outer transaction, which is undone at the end.
    """
    connection = engine.connect()
    transaction = connection.begin()
    try:

        def sessions() -> Session:
            return Session(bind=connection, join_transaction_mode="create_savepoint")

        yield sessions
    finally:
        transaction.rollback()
        connection.close()


def pick_employee(session: Session, profile: EmployeeProfile, today: date) -> uuid.UUID:
    """A deterministic seeded employee for the profile: active, past one year of service
    (so every benefit and vacation rule applies), no open requests, and at least 40
    vacation hours available, so vacation scenarios fail only for the reason they test."""
    if profile is EmployeeProfile.OFFICE_BIWEEKLY:
        conditions = [Employee.payroll_class == PayrollClass.BIWEEKLY, Shift.code == "OFFICE"]
    else:
        conditions = [
            Employee.payroll_class == PayrollClass.WEEKLY_PRODUCTION,
            Shift.code == "PROD_1ST",
        ]
    available = (
        LeaveBalance.accrued_hours + LeaveBalance.carried_over_hours - LeaveBalance.used_hours
    )
    open_request = exists().where(
        VacationRequest.employee_id == Employee.id,
        VacationRequest.status.in_([VacationRequestStatus.PENDING, VacationRequestStatus.APPROVED]),
    )
    employee_id = session.scalar(
        select(Employee.id)
        .join(Shift, Shift.id == Employee.shift_id)
        .join(
            LeaveBalance,
            (LeaveBalance.employee_id == Employee.id)
            & (LeaveBalance.leave_type == LeaveType.VACATION)
            & (LeaveBalance.year == today.year),
        )
        .where(
            *conditions,
            Employee.employment_status == EmploymentStatus.ACTIVE,
            Employee.hire_date <= today - timedelta(days=366),
            available >= Decimal(40),
            ~open_request,
        )
        .order_by(Employee.employee_number)
        .limit(1)
    )
    if employee_id is None:
        raise LookupError(f"no seeded employee fits the {profile.value} profile")
    return employee_id


# --- running one case ---------------------------------------------------------------


def _messages(agent: Agent, thread_id: str) -> list[ChatMessage]:
    values = agent.get_state({"configurable": {"thread_id": thread_id}}).values
    messages: list[ChatMessage] = values.get("messages", [])
    return messages


def _record_turn(
    say: str,
    reply: str,
    paused: bool,
    history: list[ChatMessage],
    seen: int,
    calls: list[ChatResult],
) -> TurnRecord:
    """This turn's tool calls: the ones it made, plus the ones it answered. A call made
    just before a pause gets its result in the next turn, after the employee confirms."""
    new = history[seen:]
    calls_by_id = {c.id: c for m in history for c in m.tool_calls}
    results = {m.tool_call_id: m.content or "" for m in new if m.role == "tool"}
    ids = [c.id for m in new for c in m.tool_calls] + list(results)
    tool_calls = tuple(
        ToolCallRecord(
            name=calls_by_id[call_id].name,
            arguments=calls_by_id[call_id].arguments,
            result=results.get(call_id, ""),  # empty: still waiting for confirmation
        )
        for call_id in dict.fromkeys(ids)
        if call_id in calls_by_id
    )
    retrieved = tuple(
        dict.fromkeys(
            section
            for call in tool_calls
            if call.name == "search_handbook"
            for section in parse_citations(call.result)
        )
    )
    return TurnRecord(
        say=say,
        reply=reply,
        paused=paused,
        tool_calls=tool_calls,
        retrieved_sections=retrieved,
        models=tuple(c.model for c in calls),
    )


def run_case(
    case: AgentCase,
    make_agent: MakeAgent,
    model: RecordingChatModel,
    sessions: Sessions,
    employee_id: uuid.UUID | None,
) -> AgentCaseRecord:
    """Run every turn of one case. Raises ModelOutageError if the chain went down."""
    agent = make_agent(sessions)
    thread = case.case_id
    turns: list[TurnRecord] = []
    started = time.perf_counter()
    prompt_tokens = completion_tokens = 0
    model.take()  # start clean

    for say in case.messages:
        seen = len(_messages(agent, thread))
        reply = ask(
            agent,
            thread,
            say,
            language=case.language,
            channel=EVAL_CHANNEL,
            employee_id=employee_id,
        )
        calls = model.take()
        prompt_tokens += sum(c.usage.prompt_tokens for c in calls)
        completion_tokens += sum(c.usage.completion_tokens for c in calls)
        state = agent.get_state({"configurable": {"thread_id": thread}})
        paused = any(task.interrupts for task in state.tasks)
        history = _messages(agent, thread)
        turns.append(_record_turn(say, reply, paused, history, seen, calls))

    return AgentCaseRecord(
        case_id=case.case_id,
        scenario_id=case.scenario.id,
        category=case.scenario.category,
        language=case.language.value,
        turns=tuple(turns),
        latency_ms=(time.perf_counter() - started) * 1000,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


# --- storage --------------------------------------------------------------------------


def results_path(out: Path, model_name: str) -> Path:
    """One JSONL file per model chain, named after it."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model_name.replace(" -> ", "__then__"))
    return out / f"{safe[:120]}.jsonl"


def load_case_records(path: Path) -> list[AgentCaseRecord]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [AgentCaseRecord.model_validate_json(line) for line in lines if line.strip()]


def append_case_record(path: Path, record: AgentCaseRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(record.model_dump_json() + "\n")
