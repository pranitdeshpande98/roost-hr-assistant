"""What one conversation turn did, gathered while it runs (NFR-08, NFR-11, FR-51).

`ask()` opens a turn; the model client, the graph nodes and the tools add to it through a
context variable, so nothing has to be passed down by hand. The result is numbers and
categories only, the same rule as for traces (FR-49): it never holds what anyone said.
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal

from roost.observability.prices import estimate_cost

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelCall:
    purpose: str  # "agent" or "guardrail"
    model: str  # "provider/model"
    input_tokens: int
    output_tokens: int
    latency_ms: float
    ok: bool
    fallback_position: int = 0  # 0 = first choice, 1 = first fallback, ...

    @property
    def cost_usd(self) -> Decimal:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


@dataclass(slots=True)
class ToolUse:
    name: str
    outcome: str  # "ok", "refused" (a rule or the employee said no), "error"


@dataclass(slots=True)
class TurnStats:
    channel: str
    language: str
    resumed: bool  # an answer to a confirmation question
    started: float = field(default_factory=time.perf_counter)
    route: str | None = None
    category: str | None = None
    guardrail_source: str | None = None
    model_calls: list[ModelCall] = field(default_factory=list)
    tools: list[ToolUse] = field(default_factory=list)
    searches: int = 0
    search_hits: int = 0
    # answer, routed, question (paused for YES/NO), fallback (every model failed), error
    reply_kind: str = "answer"
    unanswered: bool = False  # searched the handbook but cited nothing
    latency_ms: float = 0.0
    first_token_ms: float | None = None

    @property
    def input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.model_calls)

    @property
    def output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.model_calls)

    @property
    def cost_usd(self) -> Decimal:
        return sum((call.cost_usd for call in self.model_calls), Decimal(0))

    @property
    def agent_model(self) -> str | None:
        """The model that gave the agent's last successful answer."""
        answered = [c for c in self.model_calls if c.purpose == "agent" and c.ok]
        return answered[-1].model if answered else None

    @property
    def fallback_used(self) -> bool:
        return any(call.fallback_position > 0 and call.ok for call in self.model_calls)

    def finish(self) -> None:
        self.latency_ms = (time.perf_counter() - self.started) * 1000

    def mark_first_token(self) -> None:
        if self.first_token_ms is None:
            self.first_token_ms = (time.perf_counter() - self.started) * 1000


_current: ContextVar[TurnStats | None] = ContextVar("roost_turn", default=None)
# Who is calling the model right now: set by the graph node, read by the model client.
_purpose: ContextVar[str] = ContextVar("roost_llm_purpose", default="agent")
_fallback_position: ContextVar[int] = ContextVar("roost_llm_fallback", default=0)


def current() -> TurnStats | None:
    return _current.get()


OnTurn = Callable[[TurnStats], None]


@contextmanager
def turn(
    channel: str, language: str, *, resumed: bool, on_finish: OnTurn | None = None
) -> Iterator[TurnStats]:
    """Collect one turn's numbers; hand them to `on_finish` at the end (also after an
    error). A failure to record never breaks the conversation."""
    stats = TurnStats(channel=channel, language=language, resumed=resumed)
    token = _current.set(stats)
    try:
        yield stats
    finally:
        stats.finish()
        _current.reset(token)
        if on_finish is not None:
            try:
                on_finish(stats)
            except Exception:
                logger.exception("Could not record the turn's metrics")


@contextmanager
def purpose(name: str) -> Iterator[None]:
    token = _purpose.set(name)
    try:
        yield
    finally:
        _purpose.reset(token)


@contextmanager
def fallback_position(position: int) -> Iterator[None]:
    token = _fallback_position.set(position)
    try:
        yield
    finally:
        _fallback_position.reset(token)


def record_model_call(
    model: str, input_tokens: int, output_tokens: int, latency_ms: float, *, ok: bool
) -> ModelCall:
    call = ModelCall(
        purpose=_purpose.get(),
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        ok=ok,
        fallback_position=_fallback_position.get(),
    )
    stats = _current.get()
    if stats is not None:
        stats.model_calls.append(call)
    return call


def summary_attributes(stats: TurnStats) -> dict[str, object]:
    """The turn's span attributes."""
    return {
        "roost.reply.kind": stats.reply_kind,
        "roost.reply.unanswered": stats.unanswered,
        "roost.route": stats.route,
        "roost.guardrail.category": stats.category,
        "roost.guardrail.source": stats.guardrail_source,
        "roost.turn.input_tokens": stats.input_tokens,
        "roost.turn.output_tokens": stats.output_tokens,
        "roost.turn.cost_usd": float(stats.cost_usd),
        "gen_ai.request.model": stats.agent_model,
    }
