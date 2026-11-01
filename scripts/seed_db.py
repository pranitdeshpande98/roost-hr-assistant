"""Fill the mock HR tables with synthetic data.

Usage:
    uv run python scripts/seed_db.py                 # first time
    uv run python scripts/seed_db.py --reset         # replace existing data
    uv run python scripts/seed_db.py --seed 7 --as-of 2026-11-01
"""

import argparse
import logging
import sys
from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.seed.employees import generate_seed_data
from roost.seed.loader import is_seeded, load_seed_data, reset_hr_tables

logger = logging.getLogger("seed_db")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the mock HR tables with synthetic data.")
    parser.add_argument("--reset", action="store_true", help="delete existing HR data first")
    parser.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="reference date for hire dates and balances (default: today, UTC)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    if args.reset and settings.environment == "production":
        logger.error("Refusing to reset HR data in production.")
        return 2

    as_of: date = args.as_of or datetime.now(UTC).date()
    data = generate_seed_data(as_of=as_of, seed=args.seed)

    engine = create_engine(settings.resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            if is_seeded(session):
                if not args.reset:
                    logger.error(
                        "HR tables already contain data. Re-run with --reset to replace it."
                    )
                    return 1
                reset_hr_tables(session)
            counts = load_seed_data(session, data, holiday_years=(as_of.year, as_of.year + 1))
    finally:
        engine.dispose()

    logger.info("Seeded as of %s with seed %s:", as_of.isoformat(), args.seed)
    for table, rows in counts.items():
        logger.info("  %-20s %6d rows", table, rows)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
