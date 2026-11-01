"""Conversation memory: LangGraph checkpoints in Postgres, in their own schema."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row

# Created by an Alembic migration; LangGraph creates and migrates the tables inside it.
MEMORY_SCHEMA = "agent_memory"

# Only these classes may be rebuilt from stored checkpoints. Loading arbitrary classes
# from storage is a code-execution risk, so anything new in AgentState must be added here.
SAFE_CHECKPOINT_TYPES = [
    ("roost.llm.types", "ChatMessage"),
    ("roost.llm.types", "ToolCall"),
    ("roost.core.enums", "Language"),
    ("roost.core.enums", "Channel"),
]


def psycopg_url(sqlalchemy_url: str) -> str:
    """psycopg wants postgresql://...; SQLAlchemy URLs name the driver (postgresql+psycopg)."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def open_checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    """A checkpointer on one dedicated connection, closed on exit.

    One connection suits a single process like the terminal chat; the API (Phase 8) will
    use a connection pool instead.
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
