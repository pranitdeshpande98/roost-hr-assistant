"""Deliver email and Teams notifications from the outbox (FR-22).

Until Phase 12 connects Teams and a mail service, "delivering" writes each message as a
JSON file into a local folder, in the recipient's language, and marks it sent. Kiosk
notices are not touched: the kiosk shows them at the employee's next sign-in.

Usage:
    uv run python scripts/deliver_outbox.py
    uv run python scripts/deliver_outbox.py --folder var/outbox
"""

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.notifications.delivery import deliver_to_folder

logger = logging.getLogger("deliver_outbox")
DEFAULT_FOLDER = Path(__file__).resolve().parents[1] / "var" / "outbox"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write unsent email and Teams messages.")
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            count = deliver_to_folder(session, args.folder, now=datetime.now(UTC))
    finally:
        engine.dispose()
    logger.info("Delivered %d message(s) to %s", count, args.folder)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
