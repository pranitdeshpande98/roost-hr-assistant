"""FR-23: tell employees whose request has waited too long to check with HR in person.

A request is overdue after 3 business days without a decision, or 2 days before its first
day. Each approval round gets one notice; nothing is escalated automatically. Run it once
a day (Phase 11 schedules it).

Usage:
    uv run python scripts/send_reminders.py
    uv run python scripts/send_reminders.py --today 2027-01-08
"""

import argparse
import logging
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.leave.approvals import send_overdue_notices

logger = logging.getLogger("send_reminders")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send FR-23 overdue-decision notices.")
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            sent = send_overdue_notices(
                session, today=args.today or datetime.now(UTC).astimezone().date()
            )
    finally:
        engine.dispose()
    logger.info("Overdue notices sent: %d", len(sent))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
