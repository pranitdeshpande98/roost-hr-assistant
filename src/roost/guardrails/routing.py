"""Turn a guardrail decision into the fixed reply the employee sees (FR-06, FR-30, FR-31).

The model is never asked: each route has a reviewed template in config/messages.yaml,
followed by the employee's own HR team and phone number (handbook section 2.3).
"""

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from roost.agent.messages import MessageCatalog, MessageKey
from roost.core.enums import Language
from roost.db.models import Employee, Location
from roost.guardrails.types import ROUTE_OF, Category, Route


@dataclass(frozen=True, slots=True)
class HRContact:
    location: str  # "Georgia Plant"
    phone: str  # "(555) 010-1100"


HRContactLookup = Callable[[uuid.UUID | None], HRContact | None]

_TEMPLATE: dict[Route, MessageKey] = {
    Route.SENSITIVE: "route_sensitive",
    Route.OWN_DATA: "route_own_data",
    Route.INJECTION: "route_injection",
}


def routed_reply(
    category: Category,
    language: Language,
    messages: MessageCatalog,
    contact: HRContact | None,
) -> str:
    """The reply for a routed message, without the footer (compose_reply adds it).

    Emergencies get their own template first: 988 for self-harm, 911 for everything
    else. An injection attempt gets no HR contact: there is nothing to refer.
    """
    route = ROUTE_OF[category]
    if route is Route.ALLOWED:
        raise ValueError("an allowed message is answered by the agent, not routed")
    if route is Route.EMERGENCY:
        key: MessageKey = "route_self_harm" if category is Category.SELF_HARM else "route_danger"
    else:
        key = _TEMPLATE[route]
    parts = [messages.get(key, language)]
    if route is not Route.INJECTION:
        if contact is None:
            parts.append(messages.get("hr_contact_unknown", language))
        else:
            parts.append(
                messages.get("hr_contact", language).format(
                    location=contact.location, phone=contact.phone
                )
            )
    return "\n\n".join(parts)


def database_hr_contact(
    session_factory: Callable[[], AbstractContextManager[Session]],
) -> HRContactLookup:
    """Look up the HR team at the employee's location. None before sign-in, or if the
    employee is unknown: the reply then points to the handbook's contact table."""

    def lookup(employee_id: uuid.UUID | None) -> HRContact | None:
        if employee_id is None:
            return None
        with session_factory() as session:
            row = session.execute(
                select(Location.name, Location.hr_phone)
                .join(Employee, Employee.location_id == Location.id)
                .where(Employee.id == employee_id)
            ).one_or_none()
        return HRContact(row.name, row.hr_phone) if row else None

    return lookup
