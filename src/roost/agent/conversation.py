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
from roost.core.enums import Channel, Language
from roost.llm.types import ChatMessage


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
) -> str:
    """Run one turn on `thread_id`. The same call starts a new thread or continues one.

    Every turn resets the per-turn fields (tool_rounds, reply) and sets the language,
    which Teams may switch between messages. History carries over; `employee_id` is set
    when given (after sign-in) and otherwise kept from earlier turns.
    """
    if waiting_for_confirmation(agent, thread_id):
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
        }
        if employee_id is not None:
            turn["employee_id"] = employee_id
        result = agent.invoke(turn, {"configurable": {"thread_id": thread_id}})

    pending = result.get("__interrupt__")
    if pending:
        # Paused for confirmation: show the fixed-template question (plus footer).
        return compose_reply(str(pending[0].value), language, messages or default_messages())
    return str(result["reply"])


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
