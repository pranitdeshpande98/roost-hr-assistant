"""The notification outbox (FR-22, FR-23, ADR-0008).

Code that decides something writes a row here, in its own transaction; delivery is a
separate step. In the MVP nothing is sent: email and Teams rows are written to a local
outbox folder, and kiosk rows are shown at the employee's next sign-in.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from roost.core.enums import NotificationChannel, NotificationKind
from roost.db.base import Base
from roost.db.types import str_enum


class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index(
            "ix_notification_unsent",
            "channel",
            "recipient_id",
            postgresql_where=text("sent_at IS NULL"),
        ),
        Index("ix_notification_request", "request_id", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employee.id", ondelete="CASCADE"))
    channel: Mapped[NotificationChannel] = mapped_column(str_enum(NotificationChannel))
    kind: Mapped[NotificationKind] = mapped_column(str_enum(NotificationKind))
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vacation_request.id"))
    # Reference, days, status, an approver's comment. Never conversation content.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
