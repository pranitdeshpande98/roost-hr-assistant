"""Delete the employee's words from HR referrals handled more than 90 days ago (FR-32).

The record that a referral was sent and handled stays, for the audit trail; only the
message itself is erased. Run it once a day (Phase 11 schedules it).

Usage:
    uv run python scripts/delete_old_referrals.py
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.referrals.service import delete_old_texts

logger = logging.getLogger("delete_old_referrals")


def main() -> int:
    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            erased = delete_old_texts(session, now=datetime.now(UTC))
    finally:
        engine.dispose()
    logger.info("Referral messages deleted: %d", erased)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
