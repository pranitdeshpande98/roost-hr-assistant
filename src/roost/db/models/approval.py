"""Approvals of vacation requests (FR-20, FR-21, FR-13)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from roost.core.enums import ApprovalDecision, ApprovalStep
from roost.db.base import Base
from roost.db.types import str_enum


class Approval(Base):
    """One approval step of one round of a request.

    A request gets a supervisor step and an HR step. Any change restarts both (FR-13):
    the next round gets new rows, and earlier decisions stay on record. The supervisor
    step is assigned to a person; the HR step to the HR team at a location, so anyone on
    that team can decide it.
    """

    __tablename__ = "approval"
    __table_args__ = (
        UniqueConstraint("request_id", "round", "step"),
        CheckConstraint("num_nonnulls(assignee_id, hr_location_id) = 1", name="one_assignee"),
        CheckConstraint(
            "(decision = 'pending') = (decided_at IS NULL AND decided_by_id IS NULL)",
            name="decided_fields_match",
        ),
        CheckConstraint("round >= 1", name="round_positive"),
        Index("ix_approval_assignee_decision", "assignee_id", "decision"),
        Index("ix_approval_hr_location_decision", "hr_location_id", "decision"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vacation_request.id"))
    round: Mapped[int] = mapped_column(SmallInteger)
    step: Mapped[ApprovalStep] = mapped_column(str_enum(ApprovalStep))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee.id"))
    hr_location_id: Mapped[int | None] = mapped_column(ForeignKey("location.id"))
    decision: Mapped[ApprovalDecision] = mapped_column(
        str_enum(ApprovalDecision),
        default=ApprovalDecision.PENDING,
        server_default=ApprovalDecision.PENDING.value,
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee.id"))
    comment: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
