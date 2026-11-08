"""What the web app needs, built once at start-up (the API's composition root)."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from roost.agent.factory import build_agent
from roost.agent.memory import open_pooled_checkpointer
from roost.agent.messages import MessageCatalog, default_messages
from roost.agent.state import AgentState
from roost.api.ui_text import UIText, default_ui_text
from roost.auth.console import ConsoleAuthenticator, LocalPasswords
from roost.auth.rate_limit import AttemptLimiter
from roost.core.config import get_settings
from roost.observability.metrics import database_metrics
from roost.observability.turns import OnTurn

Agent = CompiledStateGraph[AgentState, Any, Any, Any]

# A pool sized for NFR-02 (100 concurrent sessions, 2 requests/s): requests hold a
# connection only briefly, and the agent's model calls do not hold one at all.
DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 10


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Services:
    sessions: Callable[[], AbstractContextManager[Session]]
    agent: Agent
    limiter: AttemptLimiter = field(default_factory=AttemptLimiter)
    clock: Callable[[], datetime] = utc_now
    ui: UIText = field(default_factory=default_ui_text)
    messages: MessageCatalog = field(default_factory=default_messages)
    # Local passwords until Phase 12 brings Microsoft Entra ID (ADR-0013).
    console_auth: ConsoleAuthenticator = field(default_factory=LocalPasswords)
    # Saves each turn's numbers (turn_metric). None: not recorded (tests, unless they ask).
    record_turn: OnTurn | None = None


@contextmanager
def production_services() -> Iterator[Services]:
    """Database pool, the agent with Postgres memory and its guardrail, and the limiter.
    Loads the embedding model, so start-up takes a few seconds."""
    url = get_settings().resolved_database_url.get_secret_value()
    engine = create_engine(
        url, pool_size=DB_POOL_SIZE, max_overflow=DB_MAX_OVERFLOW, pool_pre_ping=True
    )
    SQLAlchemyInstrumentor().instrument(engine=engine)  # query timings in traces, no values
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with open_pooled_checkpointer(url) as checkpointer:
            yield Services(
                sessions=factory,
                agent=build_agent(factory, checkpointer=checkpointer),
                record_turn=database_metrics(factory),
            )
    finally:
        engine.dispose()
