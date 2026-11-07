"""Tests for the fixed replies of routed messages."""

import pytest

from roost.agent.messages import default_messages
from roost.core.enums import Language
from roost.guardrails.routing import HRContact, routed_reply
from roost.guardrails.types import ROUTE_OF, Category, Route

MESSAGES = default_messages()
GEORGIA = HRContact("Georgia Plant", "(555) 010-1100")
HOTLINE = "1-800-555-0199"
SENSITIVE = [c for c in Category if ROUTE_OF[c] is Route.SENSITIVE]


@pytest.mark.parametrize("language", list(Language))
@pytest.mark.parametrize("category", SENSITIVE)
def test_every_sensitive_reply_carries_the_crisis_lines(
    category: Category, language: Language
) -> None:
    # The classifier can mistake a crisis for another sensitive topic (the holdout's
    # "I bought pills to take them all tonight" went to medical in the fallback): the
    # reply must still give 911 and 988.
    reply = routed_reply(category, language, MESSAGES, GEORGIA)

    assert "911" in reply
    assert "988" in reply
    assert HOTLINE in reply
    assert "(555) 010-1100" in reply


@pytest.mark.parametrize("language", list(Language))
def test_self_harm_leads_with_988_and_danger_with_911(language: Language) -> None:
    self_harm = routed_reply(Category.SELF_HARM, language, MESSAGES, GEORGIA)
    danger = routed_reply(Category.DANGER, language, MESSAGES, GEORGIA)

    assert self_harm.index("988") < self_harm.index("(555)")
    assert danger.index("911") < danger.index("(555)")


def test_own_data_refers_to_the_employees_own_hr_team() -> None:
    reply = routed_reply(Category.OWN_DATA, Language.EN, MESSAGES, GEORGIA)

    assert reply.startswith("I can't see your personal records")
    assert reply.endswith("Your HR team: Georgia Plant HR, (555) 010-1100.")


def test_without_a_known_employee_the_reply_points_to_the_handbook() -> None:
    reply = routed_reply(Category.HARASSMENT, Language.ES, MESSAGES, None)

    assert "sección 2.3" in reply
    assert "(555)" not in reply


def test_an_injection_attempt_gets_no_contact_details() -> None:
    reply = routed_reply(Category.INJECTION, Language.VI, MESSAGES, GEORGIA)

    assert reply.startswith("Tôi chỉ có thể giúp")
    assert "(555)" not in reply


def test_an_allowed_message_is_never_routed() -> None:
    with pytest.raises(ValueError, match="answered by the agent"):
        routed_reply(Category.NONE, Language.EN, MESSAGES, GEORGIA)
