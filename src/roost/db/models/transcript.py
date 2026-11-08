"""Conversation transcripts: the record of what was said (FR-45, FR-46, FR-48, ADR-0014).

Each turn adds the employee's message and Roost's reply exactly as shown, including a
confirmation question and the YES or NO that answered it. The kiosk draws its screen
from this record, so what the employee saw and what HR can review are the same text.

Deleted 45 days after the last message unless an HR admin places a legal hold; only HR
admins can read them, and every view is audited. Telemetry never holds this text: the
`category` column lets sensitive conversations be counted without reading them (FR-49).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from roost.core.enums import Channel, Language
from roost.db.base import Base
from roost.db.types import str_enum


class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = (
        CheckConstraint(
            "(legal_hold AND held_by_id IS NOT NULL AND held_at IS NOT NULL)"
            " OR (NOT legal_hold AND held_by_id IS NULL AND held_at IS NULL)",
            name="hold_fields_match",
        ),
        Index("ix_conversation_last_activity_at", "last_activity_at"),
        Index("ix_conversation_employee", "employee_id", "last_activity_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The agent memory thread: one per kiosk sign-in.
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee.id"))
    channel: Mapped[Channel] = mapped_column(str_enum(Channel))
    language: Mapped[Language] = mapped_column(str_enum(Language))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    held_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee.id"))
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why it is held, e.g. a case number. Written by HR, never by the employee.
    hold_reason: Mapped[str | None] = mapped_column(String(200))

    messages: Mapped[list["TranscriptMessage"]] = relationship(
        back_populates="conversation",
        order_by="TranscriptMessage.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class TranscriptMessage(Base):
    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint("role IN ('employee', 'roost')", name="role_known"),
        CheckConstraint("rating IS NULL OR rating IN (-1, 1)", name="rating_up_or_down"),
        Index("ix_message_conversation", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    role: Mapped[str] = mapped_column(String(16))  # employee, roost
    # For Roost: answer, routed, question (YES/NO asked), fallback, error. For the employee:
    # message, or answer (their reply to a question).
    kind: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # The sensitive-topic category of the turn, if any: counted, never read (FR-49).
    category: Mapped[str | None] = mapped_column(String(32))
    cited_sections: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    # FR-47: the employee's thumbs up (1) or down (-1) on a Roost reply.
    rating: Mapped[int | None] = mapped_column()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
