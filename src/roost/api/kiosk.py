"""The kiosk (FR-40, FR-41, NFR-06): sign in, choose a PIN, chat, sign out.

Pages are server-rendered and work without JavaScript; htmx only makes sending a
message smoother. Every request after sign-in loads the session from the database,
which enforces the 2-minute idle limit.

Streaming (NFR-01, ADR-0014): kiosk.js sends messages with `X-Roost-Stream: 1`. The POST
then only holds the message and answers at once with an empty Roost bubble; the browser
opens /chat/stream, which runs the turn and sends the answer as it is written, then the
final, checked conversation, which replaces the bubble. Without the header (no
JavaScript, or tests), the POST runs the turn and returns the finished reply, as before.
"""

import contextvars
import json
import logging
import math
import queue
import threading
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from roost.agent.conversation import TranscriptLine, ask, waiting_for_confirmation
from roost.api import sessions as web_sessions
from roost.api.common import TEMPLATES, Db, client_address
from roost.api.common import language_of as _language
from roost.api.common import services_of as _services
from roost.api.services import Services
from roost.auth.kiosk import NotAllowedError, PinRejectedError, SignInOutcome, change_pin, sign_in
from roost.core.enums import Channel, EmploymentStatus, Language
from roost.db.models import Employee, WebSession
from roost.notifications.outbox import take_kiosk_notices
from roost.notifications.render import render
from roost.observability.turns import TurnStats
from roost.transcripts.service import conversation_for, messages_for_thread, record_turn

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 1000
router = APIRouter()


def _kiosk_id(request: Request) -> str:
    """Each kiosk is configured with its own id header; without one, its address counts."""
    configured = request.headers.get("x-kiosk-id", "").strip()
    return (configured or client_address(request))[:64]


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


def _render(request: Request, template: str, language: Language, **context: Any) -> str:
    """A template as text, for the stream's final event."""
    services = _services(request)
    return TEMPLATES.env.get_template(template).render(
        request=request,
        t=lambda key, **f: services.ui.get(key, language).format(**f),
        lang=language,
        **context,
    )


def _current(request: Request, db: Session) -> WebSession | None:
    """The live kiosk session, if any. It ends at once when the employee is no longer
    active, so a termination closes an open kiosk too."""
    token = request.cookies.get(web_sessions.COOKIE_NAME)
    session = web_sessions.load_session(db, token, now=_services(request).clock())
    if session is None:
        return None
    employee = db.get(Employee, session.employee_id)
    if employee is None or employee.employment_status is not EmploymentStatus.ACTIVE:
        db.delete(session)
        db.flush()
        return None
    return session


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
    web_sessions.set_cookie(response, web_sessions.COOKIE_NAME, token)
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
    response = RedirectResponse("/chat", status_code=303)
    token = web_sessions.rotate_token(db, session)
    web_sessions.set_cookie(response, web_sessions.COOKIE_NAME, token)
    return response


# --- chat ---------------------------------------------------------------------------


def _record(
    db: Session,
    services: Services,
    session: WebSession,
    said: str,
    reply: str | None,
    stats: TurnStats | None,
) -> None:
    """Add the turn to the transcript (FR-45), as the employee saw it."""
    now = services.clock()
    conversation = conversation_for(
        db,
        thread_id=session.thread_id,
        employee_id=session.employee_id,
        channel=Channel.KIOSK,
        language=session.language,
        now=now,
    )
    routed = stats is not None and stats.route not in (None, "allowed")
    record_turn(
        db,
        conversation,
        said=said,
        reply=reply,
        kind=stats.reply_kind if stats is not None and reply is not None else "error",
        category=stats.category if stats is not None and routed else None,
        resumed=stats.resumed if stats is not None else False,
        now=now,
    )


def _conversation_context(db: Session, services: Services, session: WebSession) -> dict[str, Any]:
    """The screen is drawn from the transcript record, so a confirmation question stays
    visible after it is answered, and the screen and the record never differ."""
    lines = [
        TranscriptLine(message.role == "employee", message.content)
        for message in messages_for_thread(db, session.thread_id)
    ]
    return {
        "lines": lines,
        "awaiting": waiting_for_confirmation(services.agent, str(session.thread_id)),
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
    context = _conversation_context(db, services, session)
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
    said = text.strip()
    streaming = request.headers.get("x-roost-stream") == "1" and _is_htmx(request)
    if said and streaming:
        session.pending_text = said  # /chat/stream picks it up and clears it
        context = _conversation_context(db, services, session)
        return _page(request, "_conversation.html", session.language, pending=said, **context)
    if said:
        finished: list[TurnStats] = []

        def on_turn(stats: TurnStats) -> None:
            finished.append(stats)
            if services.record_turn is not None:
                services.record_turn(stats)

        reply: str | None = None
        try:
            reply = ask(
                services.agent,
                str(session.thread_id),
                said,
                language=session.language,
                channel=Channel.KIOSK,
                employee_id=session.employee_id,
                messages=services.messages,
                on_turn=on_turn,
            )
        except Exception:
            # The agent already falls back on model outages; this is anything else.
            logger.exception("Kiosk turn failed")
            error = "error"
        _record(db, services, session, said, reply, finished[0] if finished else None)

    if not _is_htmx(request):  # no JavaScript: back to the page
        return RedirectResponse("/chat", status_code=303)
    context = _conversation_context(db, services, session)
    return _page(request, "_conversation.html", session.language, error=error, **context)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


class _QueueSink:
    """Streamed text, handed from the turn's thread to the response."""

    def __init__(self, events: "queue.Queue[tuple[str, str]]") -> None:
        self._events = events

    def text(self, delta: str) -> None:
        self._events.put(("token", delta))

    def reset(self) -> None:
        self._events.put(("reset", ""))


def _event(name: str, data: str) -> str:
    """One server-sent event. JSON keeps the data on a single line."""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/chat/stream")
def chat_stream(request: Request) -> Response:
    """Run the message the POST just held, streaming the answer (server-sent events).
    204 tells the browser there is nothing to stream, and not to reconnect."""
    services = _services(request)
    with services.sessions() as db:
        session = _current(request, db)
        said = session.pending_text if session is not None else None
        if session is None or session.must_change_pin or not said:
            db.commit()
            return Response(status_code=204)
        session.pending_text = None  # taken once: a reconnect does not run it again
        db.commit()
        token_hash = session.token_hash
        thread_id, employee_id, language = session.thread_id, session.employee_id, session.language

    events: queue.Queue[tuple[str, str]] = queue.Queue()

    def run_turn() -> None:
        finished: list[TurnStats] = []

        def on_turn(stats: TurnStats) -> None:
            finished.append(stats)
            if services.record_turn is not None:
                services.record_turn(stats)

        reply: str | None = None
        error = None
        try:
            reply = ask(
                services.agent,
                str(thread_id),
                said,
                language=language,
                channel=Channel.KIOSK,
                employee_id=employee_id,
                messages=services.messages,
                on_turn=on_turn,
                on_text=_QueueSink(events),
            )
        except Exception:
            logger.exception("Kiosk turn failed")
            error = "error"
        try:
            with services.sessions() as db:
                current = db.get(WebSession, token_hash)
                if current is not None:
                    _record(db, services, current, said, reply, finished[0] if finished else None)
                    context = _conversation_context(db, services, current)
                    html = _render(request, "_conversation.html", language, error=error, **context)
                else:  # signed out while it was answering: the turn is still recorded
                    html = ""
                db.commit()
        except Exception:
            logger.exception("Could not finish the kiosk turn")
            html = ""
        events.put(("done", html))

    # The turn runs in its own thread, in this request's context (so its spans belong to
    # this request's trace); the response sends whatever it produces as it arrives.
    worker = threading.Thread(target=contextvars.copy_context().run, args=(run_turn,))
    worker.start()

    def stream() -> Iterator[str]:
        while True:
            name, data = events.get()
            yield _event(name, data)
            if name == "done":
                break
        worker.join()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


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
