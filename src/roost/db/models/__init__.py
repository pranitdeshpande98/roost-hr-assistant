"""All ORM models. Importing this package registers every table on Base.metadata."""

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

__all__ = [
    "ChunkEmbedding",
    "Department",
    "DepartmentLocation",
    "Employee",
    "EmployeeRole",
    "HandbookChunk",
    "HandbookVersion",
    "Holiday",
    "LeaveBalance",
    "Location",
    "Shift",
    "VacationRequest",
    "VacationRequestDay",
]
