"""The quality numbers HR and engineering watch (FR-51, NFR-01, NFR-08, ADR-0014).

Everything comes from turn_metric (numbers and categories) and the ratings on transcript
messages; nothing here reads what anyone wrote. Turn metrics are kept 30 days, so the
report covers at most that.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, Float, case, cast, func, select
from sqlalchemy.orm import Session

from roost.db.models import Conversation, TranscriptMessage, TurnMetric

FIRST_TOKEN_TARGET_MS = 3000  # NFR-01: p95 time to the first token
# The similarity fallback deciding at all means the LLM classifier failed: warn.
SIMILARITY_WARNING_LOOKBACK = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class LanguageRow:
    language: str
    turns: int
    answers: int
    unanswered: int  # searched the handbook, cited nothing: "I don't know"
    routed: int
    fallbacks: int  # every model failed
    errors: int
    rated_up: int
    rated_down: int

    @property
    def unanswered_rate(self) -> float:
        return self.unanswered / self.answers if self.answers else 0.0

    @property
    def helpful_rate(self) -> float | None:
        rated = self.rated_up + self.rated_down
        return self.rated_up / rated if rated else None


@dataclass(frozen=True, slots=True)
class GuardrailRow:
    category: str
    source: str
    turns: int


@dataclass(frozen=True, slots=True)
class ModelRow:
    model: str
    turns: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class QualityReport:
    since: datetime
    turns: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    first_token_p95_ms: float | None
    fallback_turns: int  # answered by a fallback model
    model_failures: int
    tool_errors: int
    cost_usd: Decimal
    similarity_recent: int  # similarity-fallback decisions in the last 24 hours
    languages: list[LanguageRow] = field(default_factory=list)
    guardrail: list[GuardrailRow] = field(default_factory=list)
    models: list[ModelRow] = field(default_factory=list)

    @property
    def first_token_on_target(self) -> bool | None:
        if self.first_token_p95_ms is None:
            return None
        return self.first_token_p95_ms <= FIRST_TOKEN_TARGET_MS

    @property
    def warnings(self) -> list[str]:
        """Keys of the UI warnings to show, most urgent first."""
        found = []
        if self.similarity_recent:
            found.append("warning_similarity")
        if self.first_token_on_target is False:
            found.append("warning_first_token")
        if self.turns and self.fallback_turns / self.turns > 0.2:
            found.append("warning_fallbacks")
        return found


def _count(condition: ColumnElement[bool]) -> ColumnElement[int]:
    """How many rows meet the condition (0 when none)."""
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def quality_report(session: Session, *, since: datetime, now: datetime) -> QualityReport:
    in_period = TurnMetric.occurred_at >= since
    totals = session.execute(
        select(
            func.count(),
            func.percentile_cont(0.5).within_group(cast(TurnMetric.latency_ms, Float)),
            func.percentile_cont(0.95).within_group(cast(TurnMetric.latency_ms, Float)),
            func.percentile_cont(0.95).within_group(cast(TurnMetric.first_token_ms, Float)),
            _count(TurnMetric.fallback_used.is_(True)),
            func.coalesce(func.sum(TurnMetric.model_failures), 0),
            func.coalesce(func.sum(TurnMetric.tool_errors), 0),
            func.coalesce(func.sum(TurnMetric.cost_usd), 0),
        ).where(in_period)
    ).one()

    by_language = {
        row.language: row
        for row in session.execute(
            select(
                TurnMetric.language,
                func.count().label("turns"),
                _count(TurnMetric.reply_kind == "answer").label("answers"),
                _count(TurnMetric.unanswered.is_(True)).label("unanswered"),
                _count(TurnMetric.reply_kind == "routed").label("routed"),
                _count(TurnMetric.reply_kind == "fallback").label("fallbacks"),
                _count(TurnMetric.reply_kind == "error").label("errors"),
            )
            .where(in_period)
            .group_by(TurnMetric.language)
        )
    }
    ratings = {
        row.language.value: row
        for row in session.execute(
            select(
                Conversation.language,
                _count(TranscriptMessage.rating == 1).label("up"),
                _count(TranscriptMessage.rating == -1).label("down"),
            )
            .join(Conversation)
            .where(TranscriptMessage.created_at >= since, TranscriptMessage.rating.is_not(None))
            .group_by(Conversation.language)
        )
    }
    languages = []
    for code in sorted(set(by_language) | set(ratings)):
        turns = by_language.get(code)
        rated = ratings.get(code)
        languages.append(
            LanguageRow(
                language=code,
                turns=turns.turns if turns else 0,
                answers=turns.answers if turns else 0,
                unanswered=turns.unanswered if turns else 0,
                routed=turns.routed if turns else 0,
                fallbacks=turns.fallbacks if turns else 0,
                errors=turns.errors if turns else 0,
                rated_up=rated.up if rated else 0,
                rated_down=rated.down if rated else 0,
            )
        )

    guardrail = [
        GuardrailRow(row.category, row.source, row.turns)
        for row in session.execute(
            select(
                TurnMetric.category,
                TurnMetric.guardrail_source.label("source"),
                func.count().label("turns"),
            )
            .where(in_period, TurnMetric.category.is_not(None))
            .group_by(TurnMetric.category, TurnMetric.guardrail_source)
            .order_by(func.count().desc(), TurnMetric.category)
        )
    ]
    models = [
        ModelRow(row.model, row.turns, row.input_tokens, row.output_tokens, row.cost)
        for row in session.execute(
            select(
                TurnMetric.model,
                func.count().label("turns"),
                func.sum(TurnMetric.input_tokens).label("input_tokens"),
                func.sum(TurnMetric.output_tokens).label("output_tokens"),
                func.sum(TurnMetric.cost_usd).label("cost"),
            )
            .where(in_period, TurnMetric.model.is_not(None))
            .group_by(TurnMetric.model)
            .order_by(func.count().desc())
        )
    ]
    similarity_recent = session.scalar(
        select(func.count()).where(
            TurnMetric.guardrail_source == "similarity",
            TurnMetric.occurred_at >= now - SIMILARITY_WARNING_LOOKBACK,
        )
    )
    count, p50, p95, first_p95, fallback, failures, tool_errors, cost = totals
    return QualityReport(
        since=since,
        turns=count,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        first_token_p95_ms=first_p95,
        fallback_turns=int(fallback),
        model_failures=int(failures),
        tool_errors=int(tool_errors),
        cost_usd=Decimal(cost),
        similarity_recent=similarity_recent or 0,
        languages=languages,
        guardrail=guardrail,
        models=models,
    )
