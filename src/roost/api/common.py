"""Pieces both web apps share: services, the per-request database transaction,
templates, and reading the language."""

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from roost.api.services import Services
from roost.api.text import kiosk_text
from roost.core.enums import Language

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
TEMPLATES.env.filters["kiosk_text"] = kiosk_text


def services_of(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def get_db(request: Request) -> Iterator[Session]:
    """One transaction per request: committed when the page is ready, rolled back on error."""
    with services_of(request).sessions() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


Db = Annotated[Session, Depends(get_db)]


def language_of(value: str | None) -> Language:
    try:
        return Language(value or "en")
    except ValueError:
        return Language.EN


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"
