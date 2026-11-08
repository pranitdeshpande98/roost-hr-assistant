"""The daily retention run (FR-45, requirements 4.7): what is deleted, and when.

| Store | Kept | Then |
|---|---|---|
| Transcripts and their agent memory | 45 days after the last message | deleted, unless on legal hold |
| Agent memory with no transcript | while its kiosk session is open | deleted |
| Turn metrics | 30 days | deleted |
| HR referral texts | 90 days after HR handled them | the words erased, the record kept |

The audit log is never touched: it is permanent (FR-24).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy.orm import Session

from roost.agent.memory import open_checkpointer
from roost.observability.metrics import delete_old_metrics
from roost.referrals.service import delete_old_texts
from roost.transcripts.service import delete_expired, threads_to_forget


class AgentMemory(Protocol):
    """Where the agent keeps its working memory (LangGraph checkpoints)."""

    def threads(self) -> list[str]: ...

    def forget(self, thread_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RetentionReport:
    conversations: int
    memory_threads: int
    metrics: int
    referral_texts: int


def apply_retention(session: Session, memory: AgentMemory, *, now: datetime) -> RetentionReport:
    """Delete what has passed its time. Transcripts first, so their memory goes with them."""
    expired = delete_expired(session, now=now)
    session.flush()
    forgotten = threads_to_forget(session, memory.threads())
    for thread_id in forgotten:
        memory.forget(thread_id)
    return RetentionReport(
        conversations=len(expired),
        memory_threads=len(forgotten),
        metrics=delete_old_metrics(session, now=now),
        referral_texts=delete_old_texts(session, now=now),
    )


class PostgresMemory:
    """AgentMemory over the LangGraph Postgres checkpointer."""

    def __init__(self, saver: PostgresSaver) -> None:
        self._saver = saver

    def threads(self) -> list[str]:
        with self._saver.conn.cursor() as cursor:  # type: ignore[union-attr]
            cursor.execute("SELECT DISTINCT thread_id FROM checkpoints")
            return [str(row["thread_id"]) for row in cursor.fetchall()]

    def forget(self, thread_id: str) -> None:
        self._saver.delete_thread(thread_id)


@contextmanager
def postgres_memory(database_url: str) -> Iterator[PostgresMemory]:
    with open_checkpointer(database_url) as saver:
        yield PostgresMemory(saver)
