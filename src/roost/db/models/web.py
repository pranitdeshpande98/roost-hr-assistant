"""Server-side web sessions for the kiosk and the approver console (ADR-0008, ADR-0013)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from roost.core.enums import Language, SessionApp
from roost.db.base import Base
from roost.db.types import str_enum


class WebSession(Base):
    """One signed-in visit to the kiosk or the approver console. The browser holds only
    a random token in an HTTP-only cookie; the database holds its SHA-256 hash, so a copy
    of this table cannot be used to take over a session. Nothing about the employee is
    ever stored in the browser."""

    __tablename__ = "web_session"
    __table_args__ = (Index("ix_web_session_last_seen_at", "last_seen_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    app: Mapped[SessionApp] = mapped_column(str_enum(SessionApp), server_default="kiosk")
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"))
    # The kiosk's X-Kiosk-Id; "console" for console sessions.
    kiosk_id: Mapped[str] = mapped_column(String(64))
    language: Mapped[Language] = mapped_column(str_enum(Language))
    # Each kiosk sign-in starts a new conversation (agent memory thread).
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    csrf_token: Mapped[str] = mapped_column(String(64))
    # Signed in with a PIN or password HR set: only the "choose your own" page (FR-41).
    must_change_pin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Kiosk streaming: the message just sent, held only until the stream picks it up
    # (a second or two), then cleared. The transcript is the record, not this.
    pending_text: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
