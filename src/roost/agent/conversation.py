"""One conversation turn: add the employee's message to a saved thread and get the reply.

If the previous turn paused to ask "submit this?", the employee's message is the answer
and resumes that tool instead of starting a new question.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from roost.agent.messages import MessageCatalog, compose_reply, default_messages
from roost.agent.state import AgentState
from roost.agent.streaming import streaming_to
from roost.core.enums import Channel, Language
from roost.guardrails.citations import cited_sections
from roost.guardrails.types import ROUTE_OF, Route
from roost.llm.types import ChatMessage, TextSink
from roost.observability import turns
from roost.observability.tracing import set_attributes, span
from roost.observability.turns import OnTurn


def _pending_question(
    agent: CompiledStateGraph[AgentState, Any, Any, Any], thread_id: str
) -> str | None:
    """The confirmation question the thread is paused on, if any."""
    state = agent.get_state({"configurable": {"thread_id": thread_id}})
    for task in state.tasks:
        for pending in task.interrupts:
            return str(pending.value)
    return None


def waiting_for_confirmation(
    agent: CompiledStateGraph[AgentState, Any, Any, Any], thread_id: str
) -> bool:
    """Whether the next message answers "submit this?" (YES / NO) instead of asking."""
    return _pending_question(agent, thread_id) is not None


def ask(
    agent: CompiledStateGraph[AgentState, Any, Any, Any],
    thread_id: str,
    question: str,
    *,
    language: Language,
    channel: Channel,
    employee_id: uuid.UUID | None = None,
    messages: MessageCatalog | None = None,
    on_turn: OnTurn | None = None,
    on_text: TextSink | None = None,
) -> str:
    """Run one turn on `thread_id`. The same call starts a new thread or continues one.

    Every turn resets the per-turn fields (tool_rounds, reply) and sets the language,
    which Teams may switch between messages. History carries over; `employee_id` is set
    when given (after sign-in) and otherwise kept from earlier turns.

    Each turn is one trace span, and its numbers (tokens, cost, tools, guardrail
    category, latency; never text) go to `on_turn` when given (NFR-11, FR-51).

    With `on_text`, the agent's answer is streamed to it while it is written; the
    returned reply is the final, checked text, which the caller shows in its place.
    """
    resumed = waiting_for_confirmation(agent, thread_id)
    with (
        turns.turn(channel.value, language.value, resumed=resumed, on_finish=on_turn) as stats,
        span(
            "roost.turn",
            {
                "roost.channel": channel.value,
                "roost.language": language.value,
                "roost.resumed": resumed,
            },
        ) as current,
    ):
        try:
            with streaming_to(on_text):
                reply, kind, answer = _run_turn(
                    agent, thread_id, question, resumed, language, channel, employee_id, messages
                )
        except Exception:
            stats.reply_kind = "error"
            raise
        stats.reply_kind = kind
        # Searched the handbook but cited nothing: the "I don't know" signal (FR-51).
        stats.unanswered = kind == "answer" and stats.searches > 0 and not cited_sections(answer)
        set_attributes(current, turns.summary_attributes(stats))
        return reply


def _run_turn(
    agent: CompiledStateGraph[AgentState, Any, Any, Any],
    thread_id: str,
    question: str,
    resumed: bool,
    language: Language,
    channel: Channel,
    employee_id: uuid.UUID | None,
    messages: MessageCatalog | None,
) -> tuple[str, str, str]:
    """The reply, what kind it is (answer, routed, question, fallback) and the agent's
    own answer text (for the citation check)."""
    if resumed:
        resume: Command[Any] = Command(resume=question)
        result = agent.invoke(resume, {"configurable": {"thread_id": thread_id}})
    else:
        turn: dict[str, Any] = {
            "messages": [ChatMessage(role="user", content=question)],
            "language": language,
            "channel": channel,
            "tool_rounds": 0,
            "reply": None,
            "guardrail": None,
            "offer_referral": False,
        }
        if employee_id is not None:
            turn["employee_id"] = employee_id
        result = agent.invoke(turn, {"configurable": {"thread_id": thread_id}})

    pending = result.get("__interrupt__")
    if pending:
        # Paused for confirmation: show the fixed-template question (plus footer).
        question_text = str(pending[0].value)
        return (
            compose_reply(question_text, language, messages or default_messages()),
            "question",
            "",
        )
    category = result.get("guardrail")
    if category is not None and ROUTE_OF[category] is not Route.ALLOWED:
        return str(result["reply"]), "routed", ""
    history = result.get("messages", [])
    last = history[-1] if history else None
    answer = last.content if last is not None and last.role == "assistant" else None
    if not answer:
        return str(result["reply"]), "fallback", ""
    return str(result["reply"]), "answer", answer


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    from_employee: bool
    text: str


def transcript(
    agent: CompiledStateGraph[AgentState, Any, Any, Any],
    thread_id: str,
    *,
    language: Language,
    messages: MessageCatalog | None = None,
) -> list[TranscriptLine]:
    """The conversation as the employee saw it: their messages and each reply with its
    footer, then a pending confirmation question if there is one. Tool calls and results
    are not shown."""
    catalog = messages or default_messages()
    state = agent.get_state({"configurable": {"thread_id": thread_id}})
    lines = []
    for message in state.values.get("messages", []):
        if message.role == "user":
            lines.append(TranscriptLine(True, message.content or ""))
        elif message.role == "assistant" and not message.tool_calls:
            # An empty answer is a model outage: compose_reply shows the fixed fallback.
            lines.append(TranscriptLine(False, compose_reply(message.content, language, catalog)))
    question = _pending_question(agent, thread_id)
    if question is not None:
        lines.append(TranscriptLine(False, compose_reply(question, language, catalog)))
    return lines
