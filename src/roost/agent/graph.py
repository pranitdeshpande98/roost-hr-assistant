"""The agent graph: call the model, run any tools it asks for, repeat, then compose the reply."""

import logging
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from roost.agent.confirmation import ConfirmationReply
from roost.agent.messages import MessageCatalog, compose_reply, default_messages
from roost.agent.prompts import system_prompt_for
from roost.agent.state import AgentState
from roost.agent.tools import ToolContext, ToolRegistry
from roost.llm.errors import LLMError
from roost.llm.types import ChatMessage, ChatModel

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3
MAX_HISTORY_TURNS = 10  # employee messages (with their replies) sent to the model


def recent_turns(messages: list[ChatMessage], max_turns: int) -> list[ChatMessage]:
    """The last `max_turns` turns. Cuts only at an employee message, so a tool call is
    never separated from its result (providers reject that)."""
    starts = [i for i, message in enumerate(messages) if message.role == "user"]
    if len(starts) <= max_turns:
        return messages
    return messages[starts[-max_turns] :]


def ask_employee(question: str) -> ConfirmationReply:
    """Pause the graph and show `question`; the employee's next message resumes it."""
    return ConfirmationReply.from_text(str(interrupt(question)))


def build_graph(
    model: ChatModel,
    tools: ToolRegistry,
    *,
    system_prompt: Callable[[AgentState, date], str] = system_prompt_for,
    messages: MessageCatalog | None = None,
    max_tool_rounds: int = MAX_TOOL_ROUNDS,
    max_history_turns: int = MAX_HISTORY_TURNS,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    clock: Callable[[], date] = date.today,
) -> CompiledStateGraph[AgentState, Any, Any, Any]:
    """Wire the model-and-tools loop. Dependencies are passed in, so tests use fakes.

    With a checkpointer, state is saved after every step under the caller's thread_id,
    so the next turn continues the conversation, and tools can pause it to ask the
    employee for confirmation (which needs a checkpointer to resume).
    """
    catalog = messages or default_messages()

    def call_model(state: AgentState) -> dict[str, Any]:
        # Out of tool rounds: stop offering tools, so the model has to answer.
        offer = tools.specs if state.tool_rounds < max_tool_rounds else None
        history = [
            ChatMessage(role="system", content=system_prompt(state, clock())),
            *recent_turns(state.messages, max_history_turns),
        ]
        try:
            result = model.chat(history, tools=offer)
        except LLMError:
            # Every model in the chain failed. Log it; finalize sends the fixed fallback.
            logger.exception("Model call failed")
            return {"messages": [ChatMessage(role="assistant", content="")]}
        return {"messages": [result.message]}

    def run_tools(state: AgentState) -> dict[str, Any]:
        # Re-runs from the top when resumed after a confirmation, so tools must not change
        # anything before they ask (checking the rules is read-only; saving comes after).
        context = ToolContext(
            employee_id=state.employee_id,
            language=state.language,
            today=clock(),
            confirm=ask_employee,
        )
        calls = state.messages[-1].tool_calls
        return {
            "messages": [tools.execute(call, context) for call in calls],
            "tool_rounds": state.tool_rounds + 1,
        }

    def after_model(state: AgentState) -> Literal["run_tools", "finalize"]:
        if state.messages[-1].tool_calls and state.tool_rounds < max_tool_rounds:
            return "run_tools"
        return "finalize"

    def finalize(state: AgentState) -> dict[str, Any]:
        last = state.messages[-1]
        answer = None if last.tool_calls else last.content
        return {"reply": compose_reply(answer, state.language, catalog)}

    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("run_tools", run_tools)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", after_model)
    graph.add_edge("run_tools", "call_model")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
