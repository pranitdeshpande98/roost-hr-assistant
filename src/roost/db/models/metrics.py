"""One row of numbers per conversation turn (NFR-08, FR-51, ADR-0014).

Counts, categories, tokens, cost and latency only: never what anyone wrote, and nothing
that identifies the employee (FR-49, NFR-05). Kept 30 days, like traces (section 4.7).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from roost.db.base import Base


class TurnMetric(Base):
    __tablename__ = "turn_metric"
    __table_args__ = (Index("ix_turn_metric_occurred_at", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    channel: Mapped[str] = mapped_column(String(16))
    language: Mapped[str] = mapped_column(String(8))
    resumed: Mapped[bool] = mapped_column(Boolean)
    # answer, routed, question, fallback, error
    reply_kind: Mapped[str] = mapped_column(String(16))
    route: Mapped[str | None] = mapped_column(String(16))
    category: Mapped[str | None] = mapped_column(String(32))
    guardrail_source: Mapped[str | None] = mapped_column(String(16))
    tools: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    tool_errors: Mapped[int] = mapped_column(SmallInteger, default=0)
    tool_refusals: Mapped[int] = mapped_column(SmallInteger, default=0)
    searches: Mapped[int] = mapped_column(SmallInteger, default=0)
    unanswered: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str | None] = mapped_column(String(120))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    model_failures: Mapped[int] = mapped_column(SmallInteger, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
