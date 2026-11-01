"""Mock HR system tables: organization, employees, roles and leave balances."""

import uuid
from datetime import date, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from roost.core.enums import (
    EmploymentStatus,
    Language,
    LeaveType,
    LocationKind,
    PayrollClass,
    Role,
)
from roost.db.base import Base, TimestampMixin
from roost.db.types import str_enum


class Location(Base):
    """A processing plant or the corporate office."""

    __tablename__ = "location"

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(2))
    kind: Mapped[LocationKind] = mapped_column(str_enum(LocationKind))
    timezone: Mapped[str] = mapped_column(String(64))
    hr_phone: Mapped[str] = mapped_column(String(32))


class Department(Base):
    """A company-wide department; it can operate at several locations."""

    __tablename__ = "department"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class DepartmentLocation(Base):
    """Which departments operate at which locations."""

    __tablename__ = "department_location"

    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"), primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"), primary_key=True)


class Shift(Base):
    """A work schedule. Sanitation ends after midnight, so end_time may be before start_time."""

    __tablename__ = "shift"
    __table_args__ = (
        CheckConstraint("paid_hours > 0 AND paid_hours <= 24", name="paid_hours_range"),
        CheckConstraint(
            "cardinality(workdays) > 0 AND workdays <@ ARRAY[1,2,3,4,5,6,7]::smallint[]",
            name="workdays_valid",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    paid_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    workdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))  # ISO: 1 = Monday


class Employee(TimestampMixin, Base):
    """An employee in the mock HR system."""

    __tablename__ = "employee"
    __table_args__ = (
        # The employee's department must operate at the employee's location.
        ForeignKeyConstraint(
            ["department_id", "location_id"],
            ["department_location.department_id", "department_location.location_id"],
        ),
        CheckConstraint("employee_number ~ '^[0-9]{6}$'", name="employee_number_format"),
        CheckConstraint("supervisor_id IS NULL OR supervisor_id <> id", name="not_own_supervisor"),
        CheckConstraint("hire_date > birth_date", name="hire_after_birth"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    employee_number: Mapped[str] = mapped_column(String(6), unique=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    preferred_language: Mapped[Language] = mapped_column(
        str_enum(Language), default=Language.EN, server_default=Language.EN.value
    )
    payroll_class: Mapped[PayrollClass] = mapped_column(str_enum(PayrollClass))
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("location.id"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift.id"))
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employee.id"))
    hire_date: Mapped[date] = mapped_column(Date)
    birth_date: Mapped[date] = mapped_column(Date)  # PII: used only for the PIN blocklist
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    has_teams: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    employment_status: Mapped[EmploymentStatus] = mapped_column(
        str_enum(EmploymentStatus),
        default=EmploymentStatus.ACTIVE,
        server_default=EmploymentStatus.ACTIVE.value,
    )

    location: Mapped["Location"] = relationship(lazy="raise")
    department: Mapped["Department"] = relationship(lazy="raise")
    shift: Mapped["Shift"] = relationship(lazy="raise")
    supervisor: Mapped["Employee | None"] = relationship(remote_side="Employee.id", lazy="raise")
    roles: Mapped[list["EmployeeRole"]] = relationship(lazy="raise", cascade="all, delete-orphan")


class EmployeeRole(Base):
    """An extra role held by an employee (supervisor, HR generalist, HR admin)."""

    __tablename__ = "employee_role"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(str_enum(Role), primary_key=True)


class LeaveBalance(TimestampMixin, Base):
    """Hours accrued, carried over and used, per employee, leave type and year."""

    __tablename__ = "leave_balance"
    __table_args__ = (
        CheckConstraint(
            "accrued_hours >= 0 AND carried_over_hours >= 0 AND used_hours >= 0",
            name="hours_non_negative",
        ),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="year_range"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee.id", ondelete="CASCADE"), primary_key=True
    )
    leave_type: Mapped[LeaveType] = mapped_column(str_enum(LeaveType), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    accrued_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), server_default=text("0"))
    carried_over_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), server_default=text("0"))
    used_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), server_default=text("0"))


class Holiday(Base):
    """A company holiday on its observed date (handbook Section 12)."""

    __tablename__ = "holiday"

    observed_date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
