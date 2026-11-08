"""The Roost web app: the kiosk and the approver console (ADR-0008, ADR-0013).

Run it with:  uv run uvicorn roost.api.app:app --port 8000
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy import text

from roost.api.console import router as console_router
from roost.api.kiosk import router as kiosk_router
from roost.api.services import Services, production_services
from roost.core.config import get_settings
from roost.observability.logs import configure_logging
from roost.observability.tracing import setup_tracing

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Shared kiosk (NFR-06) and OWASP basics (NFR-04): nothing cached, nothing framed, and
# scripts and styles only from this server, never inline.
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


def create_app(services: Services | None = None) -> FastAPI:
    """The app. Tests pass their own `services`; otherwise production ones are built at
    start-up and closed at shutdown."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services is not None:
            app.state.services = services
            yield
            return
        settings = get_settings()
        configure_logging(settings.log_level)
        provider = setup_tracing(
            settings.otel_exporter_otlp_endpoint, environment=settings.environment
        )
        try:
            with production_services() as built:
                app.state.services = built
                yield
        finally:
            if provider is not None:
                provider.shutdown()  # send the last spans

    # No interactive API docs: nothing here is for browsing by the public.
    app = FastAPI(title="Roost", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.get("/health")
    def health(request: Request) -> JSONResponse:
        """For the load balancer: the app is up and can reach the database."""
        try:
            with request.app.state.services.sessions() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Health check: database unreachable")
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    # A span per request: method, route, status and timing. Static files and health checks
    # are left out; no headers or bodies are recorded.
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls="static,health", exclude_spans=["receive", "send"]
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(kiosk_router)
    app.include_router(console_router)
    return app


app = create_app()
