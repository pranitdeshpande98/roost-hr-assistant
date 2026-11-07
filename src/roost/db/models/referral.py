"""What an employee asked Roost to send their HR team (FR-32, ADR-0013).

Only with the employee's explicit YES, and only their own words: the message that
raised a sensitive matter, exactly as they wrote it, never reworded by a model. Only HR
at the employee's location (or an HR admin) can read it, in the approver console; the
notice HR gets by Teams or email says nothing about it. The words are deleted 90 days
after HR marks the referral handled. Nothing ever goes to the Ethics Hotline.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from roost.core.enums import Language
from roost.db.base import Base
from roost.db.types import str_enum
from roost.guardrails.types import Category

MAX_REFERRAL_LENGTH = 1000  # the kiosk's message limit


class HRReferral(Base):
    __tablename__ = "hr_referral"
    __table_args__ = (
        CheckConstraint("text IS NOT NULL OR text_deleted_at IS NOT NULL", name="text_or_deleted"),
        CheckConstraint(
            "(handled_at IS NULL) = (handled_by_id IS NULL)", name="handled_fields_match"
        ),
        CheckConstraint(
            "text_deleted_at IS NULL OR handled_at IS NOT NULL", name="deleted_only_when_handled"
        ),
        Index("ix_hr_referral_open", "location_id", postgresql_where=text("handled_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employee.id"))
    # The employee's location when they asked: whose HR team it went to.
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    category: Mapped[Category] = mapped_column(str_enum(Category))
    language: Mapped[Language] = mapped_column(str_enum(Language))
    text: Mapped[str | None] = mapped_column(String(MAX_REFERRAL_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handled_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee.id"))
    text_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
