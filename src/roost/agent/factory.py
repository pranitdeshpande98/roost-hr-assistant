"""Assemble the production agent from its real parts. One place, used by every entry point."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from roost.agent.graph import Screen, build_graph
from roost.agent.messages import default_messages
from roost.agent.state import AgentState
from roost.agent.tools import ToolRegistry
from roost.guardrails.factory import build_input_guardrail
from roost.guardrails.routing import database_hr_contact
from roost.llm.client import build_chat_model
from roost.llm.providers import get_llm_settings
from roost.llm.types import ChatModel
from roost.rag.embeddings import EMBEDDING_MODELS, Embedder, SentenceTransformerEmbedder
from roost.referrals.service import database_referrals
from roost.tools.handbook import HANDBOOK_EMBEDDING_MODEL, HandbookSearchTool, database_search
from roost.tools.payroll import PayScheduleTool
from roost.tools.vacation import vacation_tools


def build_agent(
    session_factory: Callable[[], AbstractContextManager[Session]],
    *,
    model: ChatModel | None = None,
    embedder: Embedder | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    clock: Callable[[], date] = date.today,
    guardrails: bool = True,
    guardrail_model: ChatModel | None = None,
    screen: Screen | None = None,
) -> CompiledStateGraph[AgentState, Any, Any, Any]:
    """The agent with the ADR-0009 model chain and bge-m3 handbook search.

    Pass `sessionmaker(engine)` as the session factory. `model` and `embedder` can be
    swapped for fakes in tests; by default the model chain comes from AGENT_MODELS and the
    embedder loads bge-m3 (slow the first time). Pass a checkpointer for multi-turn memory
    and for vacation confirmations, which pause the conversation until the employee replies.

    The input guardrail (ADR-0012) screens every new message. By default its classifier
    chain comes from GUARDRAIL_MODELS; pass `guardrail_model` or a ready `screen` to swap
    it, or `guardrails=False` to run without it (unit tests of the agent loop only).
    """
    chat_model = model or build_chat_model(get_llm_settings().agent_model_refs)
    query_embedder = embedder or SentenceTransformerEmbedder(
        EMBEDDING_MODELS[HANDBOOK_EMBEDDING_MODEL]
    )
    search = database_search(session_factory, query_embedder)
    messages = default_messages()
    tools = ToolRegistry(
        [
            HandbookSearchTool(search),
            *vacation_tools(session_factory, messages),
            PayScheduleTool(session_factory),
        ]
    )
    if guardrails and screen is None:
        screen = build_input_guardrail(
            query_embedder,
            guardrail_model or build_chat_model(get_llm_settings().guardrail_model_refs),
        )
    return build_graph(
        chat_model,
        tools,
        messages=messages,
        checkpointer=checkpointer,
        clock=clock,
        screen=screen if guardrails else None,
        hr_contact=database_hr_contact(session_factory),
        # FR-32: with the employee's YES, their words go to their HR team.
        refer=database_referrals(session_factory) if guardrails else None,
    )
