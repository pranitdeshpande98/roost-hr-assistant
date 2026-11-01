"""Tests for roost.agent.confirmation."""

import pytest

from roost.agent.confirmation import Confirmation, ConfirmationReply, parse_confirmation


@pytest.mark.parametrize(
    "reply", ["yes", "YES", " Yes! ", "y", "ok", "sí", "Si.", "có", "Vâng", "đồng ý"]
)
def test_clear_yes_in_every_language(reply: str) -> None:
    assert parse_confirmation(reply) is Confirmation.YES


@pytest.mark.parametrize("reply", ["no", "NO.", "n", "cancel", "không", "hủy"])
def test_clear_no_in_every_language(reply: str) -> None:
    assert parse_confirmation(reply) is Confirmation.NO


@pytest.mark.parametrize(
    "reply",
    ["yes but make Friday a half day", "sure", "maybe", "", "yes no", "what is my balance?"],
)
def test_anything_else_is_unclear_and_never_consent(reply: str) -> None:
    assert parse_confirmation(reply) is Confirmation.UNCLEAR


def test_reply_keeps_the_employees_exact_words() -> None:
    reply = ConfirmationReply.from_text("yes but make Friday a half day")

    assert reply.answer is Confirmation.UNCLEAR
    assert reply.text == "yes but make Friday a half day"
