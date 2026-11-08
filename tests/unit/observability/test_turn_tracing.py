"""Every turn traced (NFR-11) with numbers and categories only (FR-49, NFR-05)."""

import io
import logging
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from opentelemetry.trace import StatusCode

from roost.agent.confirmation import Confirmation
from roost.agent.conversation import ask
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.core.enums import Channel, Language
from roost.guardrails.classifier import GuardrailDecision, Source
from roost.guardrails.types import Category
from roost.llm.errors import LLMUnavailableError
from roost.llm.fake import ScriptedChatModel, tool_reply
from roost.llm.types import ChatMessage, ChatResult, ToolCall, ToolSpec, Usage
from roost.observability.logs import JsonFormatter
from roost.observability.turns import TurnStats
from roost.referrals.offer import ReferralDraft

MESSAGES = default_messages()
EMPLOYEE = uuid.UUID("00000000-0000-0000-0000-000000000042")
PRICED = "groq/openai/gpt-oss-120b"  # $0.15 / $0.60 per million tokens
SECRET = "Rodrigo grabbed Mariela behind the freezer at Dock 7 and threatened her"


class FakeScreen:
    def __init__(self, categories: dict[str, Category] | None = None) -> None:
        self.categories = categories or {}

    def classify(self, text: str) -> GuardrailDecision:
        category = self.categories.get(text, Category.NONE)
        return GuardrailDecision(category, Source.LLM, "fake")


class LookupTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="search_handbook", description="Search.", parameters={})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        return "[§6.4] Notice\nFourteen days."


class SubmitTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="submit", description="Submit.", parameters={})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        reply = context.confirm("Submit Monday? Reply YES or NO.")
        return "SUBMITTED." if reply.answer is Confirmation.YES else "NOT SUBMITTED."


def _answer(text: str, tokens: tuple[int, int] = (1000, 200)) -> ChatResult:
    return ChatResult(
        message=ChatMessage(role="assistant", content=text),
        model=PRICED,
        finish_reason="stop",
        usage=Usage(prompt_tokens=tokens[0], completion_tokens=tokens[1]),
        latency_ms=5.0,
    )


def _agent(model: ScriptedChatModel, screen: FakeScreen | None = None, **extra: Any) -> Any:
    return build_graph(
        model,
        ToolRegistry([LookupTool(), SubmitTool()]),
        system_prompt=lambda state, today: "sys",
        messages=MESSAGES,
        checkpointer=InMemorySaver(),
        screen=screen or FakeScreen(),
        **extra,
    )


def _ask(agent: Any, text: str, turns: list[TurnStats] | None = None) -> str:
    return ask(
        agent,
        "t1",
        text,
        language=Language.ES,
        channel=Channel.KIOSK,
        employee_id=EMPLOYEE,
        on_turn=turns.append if turns is not None else None,
    )


@pytest.fixture
def json_logs() -> Iterator[io.StringIO]:
    """Everything logged during the test, as the production JSON formatter writes it."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    yield stream
    root.removeHandler(handler)
    root.setLevel(previous)


def test_a_turn_is_one_trace_with_a_span_per_step(spans: Any) -> None:
    model = ScriptedChatModel(
        [
            tool_reply(ToolCall(id="c1", name="search_handbook", arguments={"query": "notice"})),
            _answer("Catorce días [§6.4]."),
        ]
    )
    turns: list[TurnStats] = []

    _ask(_agent(model), "¿Cuánto aviso necesito?", turns)

    root = spans.one("roost.turn")
    assert root.attributes["roost.channel"] == "kiosk"
    assert root.attributes["roost.language"] == "es"
    assert root.attributes["roost.reply.kind"] == "answer"
    assert root.attributes["roost.guardrail.category"] == "none"
    names = [s.name for s in spans.all() if s.parent and s.parent.span_id == root.context.span_id]
    assert names == [
        "agent.screen",
        "agent.call_model",
        "agent.run_tools",
        "agent.call_model",
        "agent.finalize",
    ]
    assert spans.one("tool.call").attributes["roost.tool.name"] == "search_handbook"
    assert spans.one("tool.call").attributes["roost.tool.outcome"] == "ok"
    assert {s.context.trace_id for s in spans.all()} == {root.context.trace_id}

    [stats] = turns
    assert stats.searches == 1
    assert not stats.unanswered  # it cited §6.4
    assert stats.input_tokens == 1000
    assert stats.cost_usd == Decimal("0.00027")  # 1000 * 0.15 + 200 * 0.60, per million
    assert stats.agent_model == PRICED
    assert stats.latency_ms > 0


def test_an_answer_with_no_citation_after_a_search_counts_as_unanswered() -> None:
    model = ScriptedChatModel(
        [
            tool_reply(ToolCall(id="c1", name="search_handbook", arguments={"query": "x"})),
            _answer("No encontré eso en el manual."),
        ]
    )
    turns: list[TurnStats] = []

    _ask(_agent(model), "¿Hay estacionamiento?", turns)

    assert turns[0].unanswered


def test_a_routed_message_records_the_category_and_never_the_words(
    spans: Any, json_logs: io.StringIO
) -> None:
    sent: list[ReferralDraft] = []
    agent = _agent(
        ScriptedChatModel([]),
        FakeScreen({SECRET: Category.HARASSMENT}),
        hr_contact=lambda employee: None,
        refer=sent.append,
    )
    turns: list[TurnStats] = []

    _ask(agent, SECRET, turns)
    _ask(agent, "sí", turns)

    assert spans.named("roost.turn")[0].attributes["roost.guardrail.category"] == "harassment"
    assert [t.reply_kind for t in turns] == ["question", "routed"]
    assert sent  # the words went to HR, where they belong
    words = {w.strip(".,").lower() for w in SECRET.split() if len(w) >= 4}
    recorded = " ".join(
        str(value) for span in spans.all() for value in (span.attributes or {}).values()
    ).lower()
    recorded += " " + " ".join(span.name for span in spans.all()).lower()
    recorded += " " + json_logs.getvalue().lower()
    assert [w for w in words if w in recorded] == []


def test_pausing_for_a_confirmation_is_not_an_error(spans: Any) -> None:
    model = ScriptedChatModel([tool_reply(ToolCall(id="c1", name="submit", arguments={}))])
    turns: list[TurnStats] = []

    _ask(_agent(model), "Envíalo", turns)

    assert turns[0].reply_kind == "question"
    assert all(s.status.status_code is not StatusCode.ERROR for s in spans.all())


def test_a_model_outage_is_a_fallback_reply_and_counted(spans: Any) -> None:
    model = ScriptedChatModel([LLMUnavailableError("every model down")])
    turns: list[TurnStats] = []

    reply = _ask(_agent(model), "Hola", turns)

    assert reply.startswith(MESSAGES.get("no_answer", Language.ES).split(".")[0])
    assert turns[0].reply_kind == "fallback"
    assert [c.ok for c in turns[0].model_calls] == [False]


def test_a_failure_to_record_never_breaks_the_conversation() -> None:
    def broken(stats: TurnStats) -> None:
        raise RuntimeError("database down")

    model = ScriptedChatModel([_answer("Hola.")])
    agent = _agent(model)

    reply = ask(agent, "t1", "Hola", language=Language.ES, channel=Channel.KIOSK, on_turn=broken)

    assert reply.startswith("Hola.")


def test_json_logs_carry_the_trace_id(spans: Any, json_logs: io.StringIO) -> None:
    from roost.observability.tracing import span

    with span("test.parent") as current:
        logging.getLogger("roost.test").warning("Removed %d unsupported citation(s)", 1)

    line = json_logs.getvalue().strip().splitlines()[-1]
    assert f'"trace_id": "{current.get_span_context().trace_id:032x}"' in line
    assert '"message": "Removed 1 unsupported citation(s)"' in line
