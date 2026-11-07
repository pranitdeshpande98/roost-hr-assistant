"""Kiosk sessions on the server (ADR-0008, ADR-0013; FR-40, NFR-06).

The cookie holds only a random token. The 2-minute idle limit is enforced here, on
every request: a page the browser kept open, or a request replayed later, finds the
session gone. Sign-out deletes the row.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from roost.core.enums import Language
from roost.db.models import KioskSession

IDLE_LIMIT = timedelta(minutes=2)  # FR-40
COOKIE_NAME = "roost_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(
    db: Session,
    *,
    employee_id: uuid.UUID,
    kiosk_id: str,
    language: Language,
    must_change_pin: bool,
    now: datetime,
) -> tuple[str, KioskSession]:
    """A new session and its token (for the cookie; it is not stored). Any other session
    the employee left open on this kiosk ends: one person, one session per kiosk."""
    db.execute(
        delete(KioskSession).where(
            KioskSession.employee_id == employee_id, KioskSession.kiosk_id == kiosk_id
        )
    )
    token = secrets.token_urlsafe(32)
    session = KioskSession(
        token_hash=_hash(token),
        employee_id=employee_id,
        kiosk_id=kiosk_id,
        language=language,
        thread_id=uuid.uuid4(),
        csrf_token=secrets.token_urlsafe(32),
        must_change_pin=must_change_pin,
        last_seen_at=now,
    )
    db.add(session)
    db.flush()
    return token, session


def load_session(db: Session, token: str | None, *, now: datetime) -> KioskSession | None:
    """The live session for `token`, touched; None if there is none or it went idle
    (an idle session is deleted on the spot)."""
    if not token:
        return None
    session = db.get(KioskSession, _hash(token))
    if session is None:
        return None
    if now - session.last_seen_at > IDLE_LIMIT:
        db.delete(session)
        db.flush()
        return None
    session.last_seen_at = now
    db.flush()
    return session


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(KioskSession).where(KioskSession.token_hash == _hash(token)))


def purge_idle_sessions(db: Session, *, now: datetime) -> int:
    """Delete every idle session; run periodically so abandoned rows do not pile up."""
    deleted = db.execute(
        delete(KioskSession)
        .where(KioskSession.last_seen_at < now - IDLE_LIMIT)
        .returning(KioskSession.token_hash)
    )
    return len(deleted.all())


def csrf_ok(session: KioskSession, submitted: str | None) -> bool:
    """Every form carries the session's CSRF token; a forged cross-site form does not."""
    return submitted is not None and secrets.compare_digest(session.csrf_token, submitted)
