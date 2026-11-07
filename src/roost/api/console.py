"""The approver console (FR-21, FR-43): supervisors and HR sign in, see only the requests
routed to them, and approve or deny with an optional comment.

Plain server-rendered forms, no JavaScript needed. Every request loads the session from
the database (15-minute idle limit) and checks that the person may still use the
console, so taking a role away ends access at once. The pages never show a balance
(FR-06): an approver decides on dates, not on hours left.
"""

import math
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.api import sessions as web_sessions
from roost.api.common import TEMPLATES, Db, client_address, language_of, services_of
from roost.auth.console import (
    PasswordRejectedError,
    change_password,
    may_use_console,
)
from roost.auth.kiosk import NotAllowedError, SignInOutcome
from roost.core.enums import ApprovalDecision, Language, SessionApp
from roost.db.models import Approval, Employee, VacationRequest, WebSession
from roost.leave.approvals import (
    ApprovalError,
    NotYourApprovalError,
    PendingApproval,
    decide,
    pending_for,
)

PREFIX = "/console"
HISTORY_LIMIT = 50
router = APIRouter(prefix=PREFIX)


def _page(
    request: Request,
    template: str,
    language: Language,
    status: int = 200,
    *,
    signed_in: bool = True,
    **context: Any,
) -> HTMLResponse:
    services = services_of(request)
    return TEMPLATES.TemplateResponse(
        request,
        template,
        {
            "t": lambda key, **f: services.ui.get(key, language).format(**f),
            "lang": language,
            "signed_in": signed_in,
            "title_key": "console_title",
            "privacy_key": "console_privacy_notice",
            "idle_seconds": int(web_sessions.CONSOLE_IDLE_LIMIT.total_seconds()),
            "sign_out_action": f"{PREFIX}/sign-out",
        }
        | context,
        status_code=status,
    )


def _current(request: Request, db: Session) -> WebSession | None:
    session = web_sessions.load_session(
        db,
        request.cookies.get(web_sessions.CONSOLE_COOKIE_NAME),
        now=services_of(request).clock(),
        app=SessionApp.CONSOLE,
    )
    if session is not None and not may_use_console(db, session.employee_id):
        db.delete(session)
        db.flush()
        return None
    return session


def _to_sign_in(language: Language = Language.EN) -> Response:
    response = RedirectResponse(f"{PREFIX}/sign-in?lang={language.value}", status_code=303)
    response.delete_cookie(web_sessions.CONSOLE_COOKIE_NAME, path=PREFIX)
    return response


def _ready(request: Request, db: Session) -> WebSession | Response:
    """The session, or where to send the browser instead (sign-in, or choose a password)."""
    session = _current(request, db)
    if session is None:
        return _to_sign_in()
    if session.must_change_pin:
        return RedirectResponse(f"{PREFIX}/change-password", status_code=303)
    return session


# --- sign in ------------------------------------------------------------------------


@router.get("")
def home(request: Request, db: Db) -> Response:
    session = _ready(request, db)
    if isinstance(session, Response):
        return session
    return RedirectResponse(f"{PREFIX}/queue", status_code=303)


@router.get("/sign-in")
def sign_in_page(request: Request, db: Db, lang: str | None = None) -> Response:
    if _current(request, db) is not None:
        return RedirectResponse(PREFIX, status_code=303)
    return _page(request, "console/sign_in.html", language_of(lang), signed_in=False)


@router.post("/sign-in")
def sign_in_submit(
    request: Request,
    db: Db,
    employee_number: Annotated[str, Form(max_length=20)] = "",
    password: Annotated[str, Form(max_length=200)] = "",
    language: Annotated[str, Form(max_length=5)] = "en",
) -> Response:
    services = services_of(request)
    lang = language_of(language)
    address = client_address(request)
    key = f"console:{address}"

    def refuse(status: int, error: str, minutes: int = 0) -> Response:
        return _page(
            request, "console/sign_in.html", lang, status, signed_in=False, error=(error, minutes)
        )

    if not services.limiter.allow(key):
        return refuse(
            429, "rate_limited", max(1, math.ceil(services.limiter.retry_after(key) / 60))
        )

    web_sessions.purge_idle_sessions(db, now=services.clock())
    result = services.console_auth.sign_in(db, employee_number, password, now=services.clock())
    if result.outcome is SignInOutcome.LOCKED and result.locked_until is not None:
        seconds = (result.locked_until - services.clock()).total_seconds()
        return refuse(423, "locked", max(1, math.ceil(seconds / 60)))
    if not result.signed_in or result.employee_id is None:
        return refuse(401, "console_invalid")

    token, session = web_sessions.start_session(
        db,
        employee_id=result.employee_id,
        kiosk_id=address[:64],
        language=lang,
        must_change_pin=result.outcome is SignInOutcome.MUST_CHANGE_PIN,
        now=services.clock(),
        app=SessionApp.CONSOLE,
    )
    target = "change-password" if session.must_change_pin else "queue"
    response = RedirectResponse(f"{PREFIX}/{target}", status_code=303)
    response.set_cookie(
        web_sessions.CONSOLE_COOKIE_NAME,
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        path=PREFIX,  # never sent to the kiosk pages
    )
    return response


# --- choose a password (first sign-in) ----------------------------------------------


@router.get("/change-password")
def change_password_page(request: Request, db: Db) -> Response:
    session = _current(request, db)
    if session is None:
        return _to_sign_in()
    return _page(request, "console/change_password.html", session.language, csrf=session.csrf_token)


@router.post("/change-password")
def change_password_submit(
    request: Request,
    db: Db,
    current_password: Annotated[str, Form(max_length=200)] = "",
    new_password: Annotated[str, Form(max_length=200)] = "",
    repeat_password: Annotated[str, Form(max_length=200)] = "",
    csrf: Annotated[str, Form(max_length=100)] = "",
) -> Response:
    session = _current(request, db)
    if session is None:
        return _to_sign_in()

    def again(status: int, error: str) -> Response:
        return _page(
            request,
            "console/change_password.html",
            session.language,
            status,
            csrf=session.csrf_token,
            error=error,
        )

    if not web_sessions.csrf_ok(session, csrf):
        return again(403, "error")
    if new_password != repeat_password:
        return again(400, "passwords_differ")
    try:
        change_password(db, session.employee_id, current_password, new_password)
    except NotAllowedError:
        return again(400, "wrong_current_password")
    except PasswordRejectedError as rejected:
        return again(400, f"password_{rejected.problem.value}")
    session.must_change_pin = False
    return RedirectResponse(f"{PREFIX}/queue", status_code=303)


# --- the queue and one request --------------------------------------------------------


def _row(item: PendingApproval) -> dict[str, Any]:
    return {
        "id": item.approval.id,
        "reference": item.request.reference_code,
        "employee": f"{item.employee.first_name} {item.employee.last_name}",
        "employee_number": item.employee.employee_number,
        "days": [(d.day.isoformat(), d.portion.value) for d in item.request.days],
        "step": item.approval.step.value,
        "waiting_since": item.approval.created_at.date().isoformat(),
    }


@router.get("/queue")
def queue(request: Request, db: Db, done: str | None = None) -> Response:
    session = _ready(request, db)
    if isinstance(session, Response):
        return session
    rows = [_row(item) for item in pending_for(db, session.employee_id)]
    done_key = f"done_{done}" if done in ("approved", "denied") else None
    return _page(
        request, "console/queue.html", session.language, rows=rows, done=done_key, nav="queue"
    )


def _waiting(db: Session, approver: uuid.UUID, approval_id: int) -> PendingApproval | None:
    """The approval, only if it is in this approver's queue right now (FR-43)."""
    return next((p for p in pending_for(db, approver) if p.approval.id == approval_id), None)


def _not_waiting(request: Request, session: WebSession) -> Response:
    return _page(request, "console/message.html", session.language, 404, message="not_waiting")


@router.get("/approvals/{approval_id}")
def approval_page(request: Request, db: Db, approval_id: int) -> Response:
    session = _ready(request, db)
    if isinstance(session, Response):
        return session
    item = _waiting(db, session.employee_id, approval_id)
    if item is None:
        return _not_waiting(request, session)
    return _page(
        request,
        "console/approval.html",
        session.language,
        item=_row(item),
        csrf=session.csrf_token,
        nav="queue",
    )


@router.post("/approvals/{approval_id}")
def approval_decide(
    request: Request,
    db: Db,
    approval_id: int,
    decision: Annotated[str, Form(max_length=10)] = "",
    comment: Annotated[str, Form(max_length=500)] = "",
    csrf: Annotated[str, Form(max_length=100)] = "",
) -> Response:
    session = _ready(request, db)
    if isinstance(session, Response):
        return session
    if not web_sessions.csrf_ok(session, csrf):
        return _page(request, "console/message.html", session.language, 403, message="error")
    chosen = {"approve": ApprovalDecision.APPROVED, "deny": ApprovalDecision.DENIED}.get(decision)
    if chosen is None or _waiting(db, session.employee_id, approval_id) is None:
        return _not_waiting(request, session)
    try:
        decide(
            db,
            approval_id,
            session.employee_id,
            chosen,
            comment=comment,
            now=services_of(request).clock(),
        )
    except (ApprovalError, NotYourApprovalError):
        return _not_waiting(request, session)
    return RedirectResponse(f"{PREFIX}/queue?done={chosen.value}", status_code=303)


# --- history ----------------------------------------------------------------------------


@router.get("/history")
def history(request: Request, db: Db) -> Response:
    session = _ready(request, db)
    if isinstance(session, Response):
        return session
    rows = db.execute(
        select(Approval, VacationRequest, Employee)
        .join(VacationRequest, VacationRequest.id == Approval.request_id)
        .join(Employee, Employee.id == VacationRequest.employee_id)
        .where(Approval.decided_by_id == session.employee_id)
        .order_by(Approval.decided_at.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    decided: list[dict[str, Any]] = []
    for approval, vacation, employee in rows:
        at: datetime | None = approval.decided_at
        decided.append(
            {
                "reference": vacation.reference_code,
                "employee": f"{employee.first_name} {employee.last_name}",
                "days": [(d.day.isoformat(), d.portion.value) for d in vacation.days],
                "step": approval.step.value,
                "decision": approval.decision.value,
                "decided": at.date().isoformat() if at else "",
            }
        )
    return _page(request, "console/history.html", session.language, rows=decided, nav="history")


# --- sign out -----------------------------------------------------------------------


@router.post("/sign-out")
def sign_out(request: Request, db: Db) -> Response:
    """No CSRF check: signing someone out is harmless, and the idle timer must work."""
    session = _current(request, db)
    language = session.language if session else Language.EN
    web_sessions.end_session(db, request.cookies.get(web_sessions.CONSOLE_COOKIE_NAME))
    response = _to_sign_in(language)
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    return response
