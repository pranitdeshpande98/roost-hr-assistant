"""All ORM models. Importing this package registers every table on Base.metadata."""

from roost.db.models.approval import Approval
from roost.db.models.audit import AuditLog
from roost.db.models.auth import ConsoleCredential, KioskCredential
from roost.db.models.handbook import ChunkEmbedding, HandbookChunk, HandbookVersion
from roost.db.models.hr import (
    Department,
    DepartmentLocation,
    Employee,
    EmployeeRole,
    Holiday,
    LeaveBalance,
    Location,
    Shift,
)
from roost.db.models.leave import VacationRequest, VacationRequestDay
from roost.db.models.notification import Notification
from roost.db.models.referral import HRReferral
from roost.db.models.web import WebSession

__all__ = [
    "Approval",
    "AuditLog",
    "ChunkEmbedding",
    "ConsoleCredential",
    "Department",
    "DepartmentLocation",
    "Employee",
    "EmployeeRole",
    "HRReferral",
    "HandbookChunk",
    "HandbookVersion",
    "Holiday",
    "KioskCredential",
    "LeaveBalance",
    "Location",
    "Notification",
    "Shift",
    "VacationRequest",
    "VacationRequestDay",
    "WebSession",
]
