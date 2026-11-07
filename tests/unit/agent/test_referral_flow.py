"""The FR-32 offer in the real graph: the sensitive reply, the question, and only YES sends."""

import uuid
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from roost.agent.conversation import ask, transcript, waiting_for_confirmation
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolRegistry
from roost.core.enums import Channel, Language
from roost.guardrails.classifier import GuardrailDecision, Source
from roost.guardrails.routing import HRContact
from roost.guardrails.types import Category
from roost.llm.fake import ScriptedChatModel
from roost.referrals.offer import ReferralDraft

MESSAGES = default_messages()
EMPLOYEE = uuid.UUID("00000000-0000-0000-0000-000000000042")
WORDS = "My lead grabbed my arm twice this week.\nI am scared to go back."
OFFER_EN = MESSAGES.get("referral_offer", Language.EN)


class FakeScreen:
    def __init__(self, categories: dict[str, Category]) -> None:
        self.categories = categories

    def classify(self, text: str) -> GuardrailDecision:
        return GuardrailDecision(self.categories.get(text, Category.NONE), Source.LLM, "fake")


class Outbox:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[ReferralDraft] = []
        self.fail = fail

    def __call__(self, draft: ReferralDraft) -> None:
        if self.fail:
            raise RuntimeError("database down")
        self.sent.append(draft)


def _agent(outbox: Outbox, categories: dict[str, Category] | None = None) -> Any:
    return build_graph(
        ScriptedChatModel([]),  # the model is never asked
        ToolRegistry([]),
        system_prompt=lambda state, today: "sys",
        messages=MESSAGES,
        checkpointer=InMemorySaver(),
        screen=FakeScreen(categories or {WORDS: Category.HARASSMENT}),
        hr_contact=lambda employee: HRContact("Arkansas Plant", "(555) 010-2200"),
        refer=outbox,
    )


def _turn(agent: Any, text: str, language: Language = Language.EN, **extra: Any) -> str:
    return ask(
        agent,
        "t1",
        text,
        language=language,
        channel=Channel.KIOSK,
        employee_id=extra.get("employee_id", EMPLOYEE),
    )


def test_the_sensitive_reply_and_the_offer_come_together() -> None:
    agent = _agent(Outbox())

    reply = _turn(agent, WORDS)

    assert "Ethics Hotline at 1-800-555-0199" in reply
    assert "Your HR team: Arkansas Plant HR, (555) 010-2200." in reply
    assert OFFER_EN in reply
    assert waiting_for_confirmation(agent, "t1")


@pytest.mark.parametrize("answer", ["YES", "yes", "Sí", "CÓ"])
def test_yes_sends_exactly_the_employees_words(answer: str) -> None:
    outbox = Outbox()
    agent = _agent(outbox)
    _turn(agent, WORDS)

    reply = _turn(agent, answer)

    assert outbox.sent == [ReferralDraft(EMPLOYEE, Category.HARASSMENT, Language.EN, WORDS)]
    assert reply.startswith(MESSAGES.get("referral_sent", Language.EN))
    assert not waiting_for_confirmation(agent, "t1")


def test_no_sends_nothing_and_says_so() -> None:
    outbox = Outbox()
    agent = _agent(outbox)
    _turn(agent, WORDS)

    reply = _turn(agent, "no")

    assert outbox.sent == []
    assert reply.startswith(MESSAGES.get("referral_not_sent", Language.EN))


def test_anything_but_a_clear_yes_sends_nothing() -> None:
    outbox = Outbox()
    agent = _agent(outbox)
    _turn(agent, WORDS)

    reply = _turn(agent, "yes but only to my plant manager")

    assert outbox.sent == []
    assert reply.startswith(MESSAGES.get("referral_unclear", Language.EN))


def test_a_failed_send_tells_the_employee_to_call() -> None:
    agent = _agent(Outbox(fail=True))
    _turn(agent, WORDS)

    reply = _turn(agent, "yes")

    assert reply.startswith(MESSAGES.get("referral_failed", Language.EN))


def test_the_offer_is_in_the_employees_language() -> None:
    agent = _agent(Outbox())

    reply = _turn(agent, WORDS, Language.VI)

    assert MESSAGES.get("referral_offer", Language.VI) in reply
    assert OFFER_EN not in reply


def test_no_offer_in_an_emergency_or_for_anonymity_or_before_sign_in() -> None:
    danger = "There is an ammonia leak and Juan collapsed"
    anonymous = "I want to report harassment anonymously"
    outbox = Outbox()
    agent = _agent(
        outbox,
        {danger: Category.DANGER, anonymous: Category.HARASSMENT, WORDS: Category.HARASSMENT},
    )

    for text in (danger, anonymous):
        reply = _turn(agent, text)
        assert OFFER_EN not in reply
        assert not waiting_for_confirmation(agent, "t1")
    signed_out = build_graph(
        ScriptedChatModel([]),
        ToolRegistry([]),
        system_prompt=lambda state, today: "sys",
        messages=MESSAGES,
        checkpointer=InMemorySaver(),
        screen=FakeScreen({WORDS: Category.HARASSMENT}),
        refer=outbox,
    )
    reply = ask(signed_out, "t2", WORDS, language=Language.EN, channel=Channel.KIOSK)
    assert OFFER_EN not in reply
    assert outbox.sent == []


def test_the_transcript_shows_the_question_the_answer_and_the_outcome() -> None:
    agent = _agent(Outbox())
    _turn(agent, WORDS)
    _turn(agent, "yes")

    lines = transcript(agent, "t1", language=Language.EN, messages=MESSAGES)

    assert [line.from_employee for line in lines] == [True, False, True, False]
    assert lines[0].text == WORDS
    assert OFFER_EN in lines[1].text
    assert lines[2].text == "yes"
    assert lines[3].text.startswith(MESSAGES.get("referral_sent", Language.EN))
