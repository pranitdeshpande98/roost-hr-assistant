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
