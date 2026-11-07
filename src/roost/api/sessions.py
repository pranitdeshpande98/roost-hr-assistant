"""Web sessions on the server, for the kiosk and the approver console (ADR-0008,
ADR-0013; FR-40, NFR-06).

The cookie holds only a random token. The idle limit (2 minutes at the kiosk, 15 in the
console) is enforced here, on every request: a page the browser kept open, or a request
replayed later, finds the session gone. Sign-out deletes the row. A session belongs to
one app: a kiosk cookie does not open the console.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, or_
from sqlalchemy.orm import Session

from roost.core.enums import Language, SessionApp
from roost.db.models import WebSession

IDLE_LIMIT = timedelta(minutes=2)  # FR-40, the shared kiosk
CONSOLE_IDLE_LIMIT = timedelta(minutes=15)  # approvers, on their own computers
IDLE_LIMITS = {SessionApp.KIOSK: IDLE_LIMIT, SessionApp.CONSOLE: CONSOLE_IDLE_LIMIT}
COOKIE_NAME = "roost_session"
CONSOLE_COOKIE_NAME = "roost_console"


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
    app: SessionApp = SessionApp.KIOSK,
) -> tuple[str, WebSession]:
    """A new session and its token (for the cookie; it is not stored). Any other session
    the employee left open in this app on this kiosk ends: one person, one session."""
    db.execute(
        delete(WebSession).where(
            WebSession.employee_id == employee_id,
            WebSession.kiosk_id == kiosk_id,
            WebSession.app == app,
        )
    )
    token = secrets.token_urlsafe(32)
    session = WebSession(
        token_hash=_hash(token),
        app=app,
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


def load_session(
    db: Session, token: str | None, *, now: datetime, app: SessionApp = SessionApp.KIOSK
) -> WebSession | None:
    """The live session of `app` for `token`, touched; None if there is none, it belongs
    to the other app, or it went idle (an idle session is deleted on the spot)."""
    if not token:
        return None
    session = db.get(WebSession, _hash(token))
    if session is None or session.app is not app:
        return None
    if now - session.last_seen_at > IDLE_LIMITS[app]:
        db.delete(session)
        db.flush()
        return None
    session.last_seen_at = now
    db.flush()
    return session


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(WebSession).where(WebSession.token_hash == _hash(token)))


def purge_idle_sessions(db: Session, *, now: datetime) -> int:
    """Delete every idle session; run periodically so abandoned rows do not pile up."""
    deleted = db.execute(
        delete(WebSession)
        .where(
            or_(
                *(
                    and_(WebSession.app == app, WebSession.last_seen_at < now - limit)
                    for app, limit in IDLE_LIMITS.items()
                )
            )
        )
        .returning(WebSession.token_hash)
    )
    return len(deleted.all())


def csrf_ok(session: WebSession, submitted: str | None) -> bool:
    """Every form carries the session's CSRF token; a forged cross-site form does not."""
    return submitted is not None and secrets.compare_digest(session.csrf_token, submitted)
