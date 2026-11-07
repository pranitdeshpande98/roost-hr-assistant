"""Kiosk sign-in credentials (FR-40, FR-41)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from roost.db.base import Base, TimestampMixin


class KioskCredential(TimestampMixin, Base):
    """An employee's kiosk PIN, stored only as an Argon2 hash. Plant HR sets and resets
    it in person; `must_change` makes the employee choose their own at first sign-in."""

    __tablename__ = "kiosk_credential"
    __table_args__ = (
        CheckConstraint("pin_hash LIKE '$argon2id$%'", name="pin_hash_is_argon2id"),
        CheckConstraint("failed_attempts >= 0", name="failed_attempts_not_negative"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True
    )
    pin_hash: Mapped[str] = mapped_column(String(200))
    must_change: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    failed_attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
