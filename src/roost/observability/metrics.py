"""Save each turn's numbers to turn_metric (NFR-08, FR-51)."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from roost.db.models import TurnMetric
from roost.observability.turns import OnTurn, TurnStats

KEEP_METRICS = timedelta(days=30)  # traces and telemetry: 30 days (requirements 4.7)


def metric_row(stats: TurnStats) -> TurnMetric:
    return TurnMetric(
        channel=stats.channel,
        language=stats.language,
        resumed=stats.resumed,
        reply_kind=stats.reply_kind,
        route=stats.route,
        category=stats.category,
        guardrail_source=stats.guardrail_source,
        tools=[tool.name for tool in stats.tools],
        tool_errors=sum(1 for tool in stats.tools if tool.outcome == "error"),
        tool_refusals=sum(1 for tool in stats.tools if tool.outcome == "refused"),
        searches=stats.searches,
        unanswered=stats.unanswered,
        model=stats.agent_model,
        fallback_used=stats.fallback_used,
        model_failures=sum(1 for call in stats.model_calls if not call.ok),
        input_tokens=stats.input_tokens,
        output_tokens=stats.output_tokens,
        cost_usd=stats.cost_usd,
        latency_ms=round(stats.latency_ms),
        first_token_ms=round(stats.first_token_ms) if stats.first_token_ms is not None else None,
    )


def database_metrics(
    session_factory: Callable[[], AbstractContextManager[Session]],
) -> OnTurn:
    """What the web app passes to ask(): one row per turn, in its own transaction."""

    def record(stats: TurnStats) -> None:
        with session_factory() as session:
            session.add(metric_row(stats))
            session.commit()

    return record


def delete_old_metrics(session: Session, *, now: datetime) -> int:
    deleted = session.execute(
        delete(TurnMetric)
        .where(TurnMetric.occurred_at < now - KEEP_METRICS)
        .returning(TurnMetric.id)
    )
    return len(deleted.all())
