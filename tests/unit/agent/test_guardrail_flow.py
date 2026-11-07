"""The input guardrail in the real graph, with a fake screen, model and tool."""

import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from roost.agent.confirmation import Confirmation
from roost.agent.conversation import ask
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.core.enums import Channel, Language
from roost.guardrails.classifier import GuardrailDecision, Source
from roost.guardrails.routing import HRContact
from roost.guardrails.types import Category
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall, ToolSpec

MESSAGES = default_messages()
EMPLOYEE = uuid.UUID("00000000-0000-0000-0000-000000000042")
EN: dict[str, Any] = {"language": Language.EN, "channel": Channel.KIOSK, "employee_id": EMPLOYEE}


class FakeScreen:
    """Returns a category per message text (default: allowed) and records what it saw."""

    def __init__(self, categories: dict[str, Category] | None = None, fail: bool = False) -> None:
        self.categories = categories or {}
        self.fail = fail
        self.seen: list[str] = []

    def classify(self, text: str) -> GuardrailDecision:
        self.seen.append(text)
        if self.fail:
            raise RuntimeError("embedder crashed")
        return GuardrailDecision(self.categories.get(text, Category.NONE), Source.LLM, "fake")


class SubmitTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="submit", description="Submit.", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        reply = context.confirm("Submit Monday? Reply YES or NO.")
        return "SUBMITTED." if reply.answer is Confirmation.YES else "NOT SUBMITTED."


def _agent(model: ScriptedChatModel, screen: FakeScreen) -> Any:
    return build_graph(
        model,
        ToolRegistry([SubmitTool()]),
        system_prompt=lambda state, today: "sys",
        messages=MESSAGES,
        checkpointer=InMemorySaver(),
        screen=screen,
        hr_contact=lambda employee: (
            HRContact("Arkansas Plant", "(555) 010-2200") if employee == EMPLOYEE else None
        ),
    )


def _state(agent: Any, thread: str) -> dict[str, Any]:
    return dict(agent.get_state({"configurable": {"thread_id": thread}}).values)


def test_a_routed_message_never_reaches_the_model() -> None:
    model = ScriptedChatModel([])  # any model call would fail the test
    agent = _agent(model, FakeScreen({"my lead grabbed me": Category.HARASSMENT}))

    reply = ask(agent, "t1", "my lead grabbed me", **EN)

    assert model.requests == []
    assert "Ethics Hotline at 1-800-555-0199" in reply
    assert "Your HR team: Arkansas Plant HR, (555) 010-2200." in reply
    assert reply.endswith(MESSAGES.get("disclaimer", Language.EN))
    assert _state(agent, "t1")["guardrail"] is Category.HARASSMENT


def test_the_reply_is_in_the_employees_language() -> None:
    agent = _agent(ScriptedChatModel([]), FakeScreen({"¿cuánto gano?": Category.OWN_DATA}))

    reply = ask(agent, "t1", "¿cuánto gano?", **(EN | {"language": Language.ES}))

    assert reply.startswith("No puedo ver sus registros personales")
    assert MESSAGES.get("binding_notice", Language.ES) in reply


def test_an_allowed_message_goes_to_the_model_and_is_labelled() -> None:
    model = ScriptedChatModel([text_reply("Ten days a year [§6.1].")])
    agent = _agent(model, FakeScreen())

    reply = ask(agent, "t1", "How many vacation days do I get?", **EN)

    assert reply.startswith("Ten days a year")
    assert _state(agent, "t1")["guardrail"] is Category.NONE


def test_every_new_message_is_screened_and_the_label_resets() -> None:
    screen = FakeScreen({"what's my salary?": Category.OWN_DATA})
    agent = _agent(ScriptedChatModel([text_reply("Paydays are Fridays [§5.1].")]), screen)

    ask(agent, "t1", "what's my salary?", **EN)
    ask(agent, "t1", "when is payday?", **EN)

    assert screen.seen == ["what's my salary?", "when is payday?"]
    assert _state(agent, "t1")["guardrail"] is Category.NONE
    # The routed exchange stays in the history the model sees on the next turn.
    roles = [m.role for m in _state(agent, "t1")["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_a_confirmation_answer_resumes_the_tool_without_being_screened() -> None:
    screen = FakeScreen({"NO": Category.INJECTION})  # would be refused if screened
    model = ScriptedChatModel(
        [tool_reply(ToolCall(id="c1", name="submit", arguments={})), text_reply("Not sent.")]
    )
    agent = _agent(model, screen)

    ask(agent, "t1", "Book Monday off", **EN)
    reply = ask(agent, "t1", "NO", **EN)

    assert screen.seen == ["Book Monday off"]
    assert reply.startswith("Not sent.")


def test_a_broken_guardrail_does_not_break_the_chat() -> None:
    model = ScriptedChatModel([text_reply("Ten days a year [§6.1].")])
    agent = _agent(model, FakeScreen(fail=True))

    reply = ask(agent, "t1", "How many vacation days do I get?", **EN)

    assert reply.startswith("Ten days a year")
    assert _state(agent, "t1")["guardrail"] is None


class SearchTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="search", description="Search.", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        return "[§6.4] Notice\nRequest at least 14 days ahead."


def test_citations_no_tool_returned_are_removed_from_the_reply() -> None:
    model = ScriptedChatModel(
        [
            tool_reply(ToolCall(id="c1", name="search", arguments={})),
            text_reply("Ask 14 days ahead [§6.4]. Your supervisor decides [§4.2]."),
            text_reply("Yes, still 14 days [§6.4] [§9.9]."),
        ]
    )
    agent = build_graph(
        model,
        ToolRegistry([SearchTool()]),
        system_prompt=lambda state, today: "sys",
        messages=MESSAGES,
        checkpointer=InMemorySaver(),
    )

    first = ask(agent, "t1", "How much notice?", **EN)
    follow_up = ask(agent, "t1", "Even in December?", **EN)  # cites the earlier search

    assert first.startswith("Ask 14 days ahead [§6.4]. Your supervisor decides.\n")
    assert follow_up.startswith("Yes, still 14 days [§6.4].\n")
    # The history holds the cleaned text too: the model never rereads its invention.
    history = _state(agent, "t1")["messages"]
    answers = [m.content for m in history if m.role == "assistant" and not m.tool_calls]
    assert answers == [
        "Ask 14 days ahead [§6.4]. Your supervisor decides.",
        "Yes, still 14 days [§6.4].",
    ]
