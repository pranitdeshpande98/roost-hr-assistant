"""Domain enumerations shared across Roost.

These values are stored in the database. Changing or removing a value requires
a migration.
"""

from enum import StrEnum


class Language(StrEnum):
    """Languages Roost supports (FR-03)."""

    EN = "en"
    ES = "es"
    VI = "vi"


class PayrollClass(StrEnum):
    """Payroll classes from handbook Section 3.1."""

    EXECUTIVE = "executive"
    BIWEEKLY = "biweekly"
    WEEKLY_IT = "weekly_it"
    WEEKLY_PRODUCTION = "weekly_production"
    DRIVER = "driver"


class LocationKind(StrEnum):
    """Whether a location is a processing plant or the corporate office."""

    PLANT = "plant"
    CORPORATE = "corporate"


class EmploymentStatus(StrEnum):
    """Whether an employee currently works for the company."""

    ACTIVE = "active"
    TERMINATED = "terminated"


class Role(StrEnum):
    """Extra permissions on top of being an employee (FR-43)."""

    SUPERVISOR = "supervisor"
    HR_GENERALIST = "hr_generalist"
    HR_ADMIN = "hr_admin"


class LeaveType(StrEnum):
    """Leave types that have a tracked balance."""

    VACATION = "vacation"
    SICK = "sick"


class HandbookStatus(StrEnum):
    """Lifecycle of a handbook version (FR-44). Only one version is published at a time."""

    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class Channel(StrEnum):
    """Where a conversation happens: a plant kiosk or Microsoft Teams."""

    KIOSK = "kiosk"
    TEAMS = "teams"


class DayPortion(StrEnum):
    """Vacation is taken in half or full days of the scheduled shift (handbook 6.3)."""

    FULL = "full"
    HALF = "half"


class VacationRequestStatus(StrEnum):
    """Where a vacation request stands (FR-12). Pending until both approvals are given."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELLED = "cancelled"


class ApprovalStep(StrEnum):
    """The two approvals a vacation request needs, in order (FR-20)."""

    SUPERVISOR = "supervisor"
    HR = "hr"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class AuditAction(StrEnum):
    """Everything the permanent audit log records (FR-24, FR-48, section 4.7)."""

    REQUEST_SUBMITTED = "request_submitted"
    REQUEST_CHANGED = "request_changed"
    REQUEST_CANCELLED = "request_cancelled"
    APPROVAL_DECIDED = "approval_decided"
    REQUEST_APPROVED = "request_approved"
    REQUEST_DENIED = "request_denied"
    HR_SUMMARY_SENT = "hr_summary_sent"
    PIN_SET = "pin_set"
    PIN_CHANGED = "pin_changed"
    PIN_LOCKED = "pin_locked"
    LEGAL_HOLD_PLACED = "legal_hold_placed"
    LEGAL_HOLD_RELEASED = "legal_hold_released"


class NotificationChannel(StrEnum):
    """How a notification reaches someone (FR-22): Teams if they have it, else email,
    else a notice at their next kiosk sign-in (drivers)."""

    TEAMS = "teams"
    EMAIL = "email"
    KIOSK = "kiosk"


class NotificationKind(StrEnum):
    APPROVAL_NEEDED = "approval_needed"  # to the supervisor, then to HR
    REQUEST_APPROVED = "request_approved"  # to the employee
    REQUEST_DENIED = "request_denied"  # to the employee
    REQUEST_CANCELLED = "request_cancelled"  # to the approvers (FR-14)
    DECISION_OVERDUE = "decision_overdue"  # to the employee: check with HR (FR-23)
    CEO_REQUEST = "ceo_request"  # to Corporate HR: no approval needed (FR-20)
