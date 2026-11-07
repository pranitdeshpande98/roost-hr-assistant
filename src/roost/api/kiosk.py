"""The kiosk (FR-40, FR-41, NFR-06): sign in, choose a PIN, chat, sign out.

Pages are server-rendered and work without JavaScript; htmx only makes sending a
message smoother. Every request after sign-in loads the session from the database,
which enforces the 2-minute idle limit.
"""

import logging
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from roost.agent.conversation import ask, transcript, waiting_for_confirmation
from roost.api import sessions as web_sessions
from roost.api.services import Services
from roost.api.text import kiosk_text
from roost.auth.kiosk import NotAllowedError, PinRejectedError, SignInOutcome, change_pin, sign_in
from roost.core.enums import Channel, Language
from roost.db.models import KioskSession
from roost.notifications.outbox import take_kiosk_notices
from roost.notifications.render import render

logger = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
TEMPLATES.env.filters["kiosk_text"] = kiosk_text

MAX_MESSAGE_LENGTH = 1000
router = APIRouter()


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def get_db(request: Request) -> Iterator[Session]:
    """One transaction per request: committed when the page is ready, rolled back on error."""
    with _services(request).sessions() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


Db = Annotated[Session, Depends(get_db)]


def _kiosk_id(request: Request) -> str:
    """Each kiosk is configured with its own id header; without one, its address counts."""
    configured = request.headers.get("x-kiosk-id", "").strip()
    client = request.client.host if request.client else "unknown"
    return (configured or client)[:64]


def _language(value: str | None) -> Language:
    try:
        return Language(value or "en")
    except ValueError:
        return Language.EN


def _page(
    request: Request, template: str, language: Language, status: int = 200, **context: Any
) -> HTMLResponse:
    services = _services(request)
    return TEMPLATES.TemplateResponse(
        request,
        template,
        {
            "t": lambda key, **f: services.ui.get(key, language).format(**f),
            "lang": language,
            # Signed-in pages carry the idle sign-out form (kiosk.js submits it).
            "signed_in": template in ("change_pin.html", "chat.html"),
        }
        | context,
        status_code=status,
    )


def _current(request: Request, db: Session) -> KioskSession | None:
    token = request.cookies.get(web_sessions.COOKIE_NAME)
    return web_sessions.load_session(db, token, now=_services(request).clock())


def _to_sign_in(language: Language = Language.EN, request: Request | None = None) -> Response:
    """Back to the sign-in page. An htmx request gets HX-Redirect instead, so the page
    replaces the screen rather than landing inside the conversation box."""
    target = f"/sign-in?lang={language.value}"
    response: Response
    if request is not None and request.headers.get("hx-request") == "true":
        response = Response(status_code=200, headers={"HX-Redirect": target})
    else:
        response = RedirectResponse(target, status_code=303)
    response.delete_cookie(web_sessions.COOKIE_NAME, path="/")
    return response


# --- sign in ------------------------------------------------------------------------


@router.get("/")
def home(request: Request, db: Db) -> Response:
    session = _current(request, db)
    if session is None:
        return _to_sign_in()
    return RedirectResponse("/change-pin" if session.must_change_pin else "/chat", status_code=303)


@router.get("/sign-in")
def sign_in_page(request: Request, db: Db, lang: str | None = None) -> Response:
    if _current(request, db) is not None:
        return RedirectResponse("/", status_code=303)
    return _page(request, "sign_in.html", _language(lang))


@router.post("/sign-in")
def sign_in_submit(
    request: Request,
    db: Db,
    employee_number: Annotated[str, Form(max_length=20)] = "",
    pin: Annotated[str, Form(max_length=20)] = "",
    language: Annotated[str, Form(max_length=5)] = "en",
) -> Response:
    services = _services(request)
    lang = _language(language)
    kiosk = _kiosk_id(request)
    if not services.limiter.allow(kiosk):
        minutes = max(1, math.ceil(services.limiter.retry_after(kiosk) / 60))
        return _page(request, "sign_in.html", lang, 429, error=("rate_limited", minutes))

    # Sessions abandoned without signing out are swept here (indexed, cheap).
    web_sessions.purge_idle_sessions(db, now=services.clock())
    result = sign_in(db, employee_number, pin, now=services.clock())
    if result.outcome is SignInOutcome.LOCKED and result.locked_until is not None:
        minutes = max(1, math.ceil((result.locked_until - services.clock()).total_seconds() / 60))
        return _page(request, "sign_in.html", lang, 423, error=("locked", minutes))
    if not result.signed_in or result.employee_id is None:
        return _page(request, "sign_in.html", lang, 401, error=("invalid", 0))

    token, session = web_sessions.start_session(
        db,
        employee_id=result.employee_id,
        kiosk_id=kiosk,
        language=lang,
        must_change_pin=result.outcome is SignInOutcome.MUST_CHANGE_PIN,
        now=services.clock(),
    )
    target = "/change-pin" if session.must_change_pin else "/chat"
    response = RedirectResponse(target, status_code=303)
    # A session cookie (no expiry date): gone when the browser closes, and useless after
    # 2 idle minutes anyway, because the server checks every request.
    response.set_cookie(
        web_sessions.COOKIE_NAME,
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


# --- choose a PIN (first sign-in) ---------------------------------------------------


@router.get("/change-pin")
def change_pin_page(request: Request, db: Db) -> Response:
    session = _current(request, db)
    if session is None:
        return _to_sign_in()
    return _page(request, "change_pin.html", session.language, csrf=session.csrf_token)


@router.post("/change-pin")
def change_pin_submit(
    request: Request,
    db: Db,
    current_pin: Annotated[str, Form(max_length=20)] = "",
    new_pin: Annotated[str, Form(max_length=20)] = "",
    repeat_pin: Annotated[str, Form(max_length=20)] = "",
    csrf: Annotated[str, Form(max_length=100)] = "",
) -> Response:
    session = _current(request, db)
    if session is None:
        return _to_sign_in()
    if not web_sessions.csrf_ok(session, csrf):
        return _page(
            request,
            "change_pin.html",
            session.language,
            403,
            csrf=session.csrf_token,
            error="error",
        )

    def again(error: str) -> Response:
        return _page(
            request, "change_pin.html", session.language, 400, csrf=session.csrf_token, error=error
        )

    if new_pin != repeat_pin:
        return again("pins_differ")
    try:
        change_pin(db, session.employee_id, current_pin, new_pin)
    except NotAllowedError:
        return again("wrong_current_pin")
    except PinRejectedError as rejected:
        return again(f"pin_{rejected.problem.value}")
    session.must_change_pin = False
    return RedirectResponse("/chat", status_code=303)


# --- chat ---------------------------------------------------------------------------


def _conversation_context(services: Services, session: KioskSession) -> dict[str, Any]:
    thread = str(session.thread_id)
    return {
        "lines": transcript(
            services.agent, thread, language=session.language, messages=services.messages
        ),
        "awaiting": waiting_for_confirmation(services.agent, thread),
        "csrf": session.csrf_token,
    }


@router.get("/chat")
def chat_page(request: Request, db: Db) -> Response:
    session = _current(request, db)
    if session is None:
        return _to_sign_in()
    if session.must_change_pin:  # FR-41: nothing else until the employee chooses a PIN
        return RedirectResponse("/change-pin", status_code=303)
    services = _services(request)
    context = _conversation_context(services, session)
    # Kiosk-only employees (no Teams, no email) get their notifications here, once (FR-22).
    notices = take_kiosk_notices(db, session.employee_id, now=services.clock())
    context["notices"] = [render(n, session.language) for n in notices]
    return _page(request, "chat.html", session.language, **context)


@router.post("/chat/messages")
def chat_send(
    request: Request,
    db: Db,
    text: Annotated[str, Form(max_length=MAX_MESSAGE_LENGTH)] = "",
    csrf: Annotated[str, Form(max_length=100)] = "",
) -> Response:
    session = _current(request, db)
    if session is None:
        return _to_sign_in(request=request)
    if session.must_change_pin:
        return RedirectResponse("/change-pin", status_code=303)
    if not web_sessions.csrf_ok(session, csrf):
        return Response(status_code=403)

    services = _services(request)
    error = None
    if text.strip():
        try:
            ask(
                services.agent,
                str(session.thread_id),
                text.strip(),
                language=session.language,
                channel=Channel.KIOSK,
                employee_id=session.employee_id,
                messages=services.messages,
            )
        except Exception:
            # The agent already falls back on model outages; this is anything else.
            logger.exception("Kiosk turn failed")
            error = "error"

    if request.headers.get("hx-request") != "true":  # no JavaScript: back to the page
        return RedirectResponse("/chat", status_code=303)
    context = _conversation_context(services, session)
    return _page(request, "_conversation.html", session.language, error=error, **context)


# --- sign out -----------------------------------------------------------------------


@router.post("/sign-out")
def sign_out(request: Request, db: Db) -> Response:
    """Ends the session on the server and tells the browser to forget everything (NFR-06).
    No CSRF check: signing someone out is harmless, and the idle timer must always work."""
    session = _current(request, db)
    language = session.language if session else Language.EN
    web_sessions.end_session(db, request.cookies.get(web_sessions.COOKIE_NAME))
    response = _to_sign_in(language)
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    return response
