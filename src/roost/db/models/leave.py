"""Vacation requests: one row per request, one row per requested day."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from roost.core.enums import DayPortion, VacationRequestStatus
from roost.db.base import Base, TimestampMixin
from roost.db.types import str_enum


class VacationRequest(TimestampMixin, Base):
    """A vacation request. `version` is SQLAlchemy's optimistic lock: an update made from
    a stale copy fails instead of silently overwriting someone else's change."""

    __tablename__ = "vacation_request"
    __table_args__ = (
        CheckConstraint("reference_code ~ '^VR-[A-Z2-9]{6}$'", name="reference_code_format"),
        Index("ix_vacation_request_employee_status", "employee_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    reference_code: Mapped[str] = mapped_column(String(9), unique=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employee.id"))
    status: Mapped[VacationRequestStatus] = mapped_column(
        str_enum(VacationRequestStatus),
        default=VacationRequestStatus.PENDING,
        server_default=VacationRequestStatus.PENDING.value,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    days: Mapped[list["VacationRequestDay"]] = relationship(
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="VacationRequestDay.day",
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy's documented form


class VacationRequestDay(Base):
    """One day of a request. Observed holidays are stored with 0 hours (handbook 12.5)."""

    __tablename__ = "vacation_request_day"
    __table_args__ = (
        CheckConstraint("hours >= 0 AND hours <= 24", name="hours_range"),
        Index("ix_vacation_request_day_day", "day"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vacation_request.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    portion: Mapped[DayPortion] = mapped_column(str_enum(DayPortion))
    hours: Mapped[Decimal] = mapped_column(Numeric(4, 2))
