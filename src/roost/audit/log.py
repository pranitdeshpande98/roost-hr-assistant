"""Write the permanent audit log (FR-24). Insert only: the database rejects anything else.

Entries are written in the caller's transaction, so an action and its audit entry are
saved together or not at all.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.orm import Session

from roost.core.enums import AuditAction
from roost.db.models import AuditLog

# Details are for identifiers and decisions. These keys would mean content was being
# logged, which FR-49 keeps out of everything but the transcript.
_FORBIDDEN_DETAIL_KEYS = frozenset({"text", "message", "content", "reply", "summary"})


def record(
    session: Session,
    action: AuditAction,
    *,
    actor: uuid.UUID | None,
    entity_type: str,
    entity_id: str,
    details: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Add one entry to the session (flushed with the caller's next flush or commit).
    `actor` is None for actions the system takes on its own."""
    details = dict(details or {})
    forbidden = _FORBIDDEN_DETAIL_KEYS & set(details)
    if forbidden:
        raise ValueError(f"audit details must not hold content: {sorted(forbidden)}")
    entry = AuditLog(
        actor_employee_id=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    session.add(entry)
    return entry
