"""The permanent, append-only audit log (FR-24)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from roost.core.enums import AuditAction
from roost.db.base import Base
from roost.db.types import str_enum

# The database keeps this table append-only: a trigger rejects UPDATE, DELETE and
# TRUNCATE (migration 50ea1cd8c8a7). Code can only insert.


class AuditLog(Base):
    """One action. No foreign keys: the entry must outlive the rows it describes.
    `details` holds identifiers and decisions only, never message content (FR-49)."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[AuditAction] = mapped_column(str_enum(AuditAction))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
