"""What the input guardrail decides about a message, before the model sees it.

Every message gets one category; the category decides the route. Routes other than
ALLOWED are answered with a reviewed template (config/messages.yaml), never by the model.
"""

from enum import StrEnum


class Route(StrEnum):
    """How a message is handled. Ordered from most to least urgent."""

    EMERGENCY = "emergency"  # 911 / 988 and plant safety first (FR-31)
    SENSITIVE = "sensitive"  # the employee's HR team and the Ethics Hotline (FR-30, FR-31)
    INJECTION = "injection"  # an attempt to change the assistant's rules or identity
    OWN_DATA = "own_data"  # the employee's balances, pay, elections or bank data (FR-06)
    ALLOWED = "allowed"  # the agent answers as usual


class Category(StrEnum):
    """Why a message was routed. Telemetry records only this, never the text (FR-49)."""

    # emergencies
    DANGER = "danger"  # fire, chemical release, someone hurt badly, threat of violence now
    SELF_HARM = "self_harm"
    # sensitive matters (FR-30)
    HARASSMENT = "harassment"
    DISCRIMINATION = "discrimination"
    RETALIATION = "retaliation"
    VIOLENCE = "violence"  # threats or violence that is not happening right now
    INJURY = "injury"
    SAFETY_HAZARD = "safety_hazard"
    MEDICAL = "medical"  # medical conditions and accommodation needs
    IMMIGRATION = "immigration"
    WAGE_DISPUTE = "wage_dispute"
    PERSONAL_CRISIS = "personal_crisis"
    # everything else
    INJECTION = "injection"
    OWN_DATA = "own_data"
    NONE = "none"


ROUTE_OF: dict[Category, Route] = {
    Category.DANGER: Route.EMERGENCY,
    Category.SELF_HARM: Route.EMERGENCY,
    Category.HARASSMENT: Route.SENSITIVE,
    Category.DISCRIMINATION: Route.SENSITIVE,
    Category.RETALIATION: Route.SENSITIVE,
    Category.VIOLENCE: Route.SENSITIVE,
    Category.INJURY: Route.SENSITIVE,
    Category.SAFETY_HAZARD: Route.SENSITIVE,
    Category.MEDICAL: Route.SENSITIVE,
    Category.IMMIGRATION: Route.SENSITIVE,
    Category.WAGE_DISPUTE: Route.SENSITIVE,
    Category.PERSONAL_CRISIS: Route.SENSITIVE,
    Category.INJECTION: Route.INJECTION,
    Category.OWN_DATA: Route.OWN_DATA,
    Category.NONE: Route.ALLOWED,
}
