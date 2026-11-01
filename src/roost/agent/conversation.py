"""One conversation turn: add the employee's message to a saved thread and get the reply.

If the previous turn paused to ask "submit this?", the employee's message is the answer
and resumes that tool instead of starting a new question.
"""

import uuid
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from roost.agent.messages import MessageCatalog, compose_reply, default_messages
from roost.agent.state import AgentState
from roost.core.enums import Channel, Language
from roost.llm.types import ChatMessage


def _waiting_for_confirmation(
    agent: CompiledStateGraph[AgentState, Any, Any, Any], thread_id: str
) -> bool:
    state = agent.get_state({"configurable": {"thread_id": thread_id}})
    return any(task.interrupts for task in state.tasks)


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
    if _waiting_for_confirmation(agent, thread_id):
        resume: Command[Any] = Command(resume=question)
        result = agent.invoke(resume, {"configurable": {"thread_id": thread_id}})
    else:
        turn: dict[str, Any] = {
            "messages": [ChatMessage(role="user", content=question)],
            "language": language,
            "channel": channel,
            "tool_rounds": 0,
            "reply": None,
        }
        if employee_id is not None:
            turn["employee_id"] = employee_id
        result = agent.invoke(turn, {"configurable": {"thread_id": thread_id}})

    pending = result.get("__interrupt__")
    if pending:
        # Paused for confirmation: show the fixed-template question (plus footer).
        return compose_reply(str(pending[0].value), language, messages or default_messages())
    return str(result["reply"])
