"""The daily retention run (FR-45): transcripts and their agent memory after 45 days
(unless on legal hold), turn metrics after 30 days, HR referral texts 90 days after HR
handled them. Phase 11 schedules it; until then run it by hand.

Usage:
    uv run python scripts/apply_retention.py
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.transcripts.retention import apply_retention, postgres_memory

logger = logging.getLogger("apply_retention")


def main() -> int:
    url = get_settings().resolved_database_url.get_secret_value()
    engine = create_engine(url)
    try:
        with postgres_memory(url) as memory, Session(engine) as session, session.begin():
            report = apply_retention(session, memory, now=datetime.now(UTC))
    finally:
        engine.dispose()
    logger.info(
        "Deleted: %d conversations, %d agent memory threads, %d turn metrics; "
        "erased %d referral texts",
        report.conversations,
        report.memory_threads,
        report.metrics,
        report.referral_texts,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
