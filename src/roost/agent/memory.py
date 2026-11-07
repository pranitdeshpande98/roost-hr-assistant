"""Conversation memory: LangGraph checkpoints in Postgres, in their own schema."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Created by an Alembic migration; LangGraph creates and migrates the tables inside it.
MEMORY_SCHEMA = "agent_memory"

# Only these classes may be rebuilt from stored checkpoints. Loading arbitrary classes
# from storage is a code-execution risk, so anything new in AgentState must be added here.
SAFE_CHECKPOINT_TYPES = [
    ("roost.llm.types", "ChatMessage"),
    ("roost.llm.types", "ToolCall"),
    ("roost.core.enums", "Language"),
    ("roost.core.enums", "Channel"),
    ("roost.guardrails.types", "Category"),
]


def psycopg_url(sqlalchemy_url: str) -> str:
    """psycopg wants postgresql://...; SQLAlchemy URLs name the driver (postgresql+psycopg)."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def open_checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    """A checkpointer on one dedicated connection, closed on exit.

    One connection suits a single process like the terminal chat; the API uses
    open_pooled_checkpointer instead.
    """
    with psycopg.connect(
        psycopg_url(database_url),
        autocommit=True,  # required by PostgresSaver
        prepare_threshold=0,  # no server-side prepared statements (safe behind poolers)
        row_factory=dict_row,
        options=f"-c search_path={MEMORY_SCHEMA}",
    ) as connection:
        saver = PostgresSaver(
            connection, serde=JsonPlusSerializer(allowed_msgpack_modules=SAFE_CHECKPOINT_TYPES)
        )
        saver.setup()  # creates or upgrades LangGraph's own tables; safe to run every time
        yield saver


@contextmanager
def open_pooled_checkpointer(database_url: str, max_size: int = 10) -> Iterator[PostgresSaver]:
    """A checkpointer on a connection pool, for the API: concurrent requests each borrow
    their own connection instead of sharing one (NFR-02)."""
    with ConnectionPool(
        psycopg_url(database_url),
        min_size=1,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={MEMORY_SCHEMA}",
        },
        open=True,
    ) as pool:
        saver = PostgresSaver(
            pool,  # type: ignore[arg-type]  # a pool of dict-row connections, as required
            serde=JsonPlusSerializer(allowed_msgpack_modules=SAFE_CHECKPOINT_TYPES),
        )
        saver.setup()
        yield saver
