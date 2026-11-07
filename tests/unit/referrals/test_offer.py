"""When Roost may offer to send HR the employee's words (FR-32)."""

import uuid

import pytest

from roost.guardrails.types import ROUTE_OF, Category, Route
from roost.referrals.offer import may_offer, wants_anonymity

EMPLOYEE = uuid.UUID("00000000-0000-0000-0000-000000000042")
SENSITIVE = [c for c in Category if ROUTE_OF[c] is Route.SENSITIVE]


@pytest.mark.parametrize("category", SENSITIVE)
def test_every_sensitive_topic_gets_the_offer(category: Category) -> None:
    assert may_offer(category, EMPLOYEE, "my lead keeps yelling at me")


@pytest.mark.parametrize(
    "category",
    [Category.DANGER, Category.SELF_HARM, Category.OWN_DATA, Category.INJECTION, Category.NONE],
)
def test_emergencies_and_other_routes_never_get_it(category: Category) -> None:
    assert not may_offer(category, EMPLOYEE, "something happened")


def test_no_offer_before_sign_in_or_without_words() -> None:
    assert not may_offer(Category.HARASSMENT, None, "my lead keeps yelling at me")
    assert not may_offer(Category.HARASSMENT, EMPLOYEE, "   ")
    assert not may_offer(None, EMPLOYEE, "my lead keeps yelling at me")


@pytest.mark.parametrize(
    "text",
    [
        "I want to report this anonymously",
        "Can I call the hotline instead?",
        "quiero denunciarlo de forma anónima",
        "sin dar mi nombre, por favor",
        "llamaré a la Línea de Ética",
        "tôi muốn báo cáo ẩn danh",
        "số đường dây nóng là gì",
    ],
)
def test_someone_who_wants_to_stay_anonymous_is_not_asked(text: str) -> None:
    assert wants_anonymity(text)
    assert not may_offer(Category.HARASSMENT, EMPLOYEE, text)


def test_ordinary_words_are_not_mistaken_for_anonymity() -> None:
    assert not wants_anonymity("my supervisor makes comments about my accent")
    assert not wants_anonymity("me lastimé la mano en la línea de producción")
