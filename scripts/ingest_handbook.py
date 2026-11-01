"""Ingest a handbook Markdown file as a new version, optionally publishing it.

Usage:
    uv run python scripts/ingest_handbook.py data/raw/handbook/handbook-v1.0.md \
        --version-label 1.0 --effective-date 2026-11-01 --publish
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.rag.ingest import HandbookAlreadyIngestedError, ingest_handbook
from roost.rag.parsing import HandbookParseError

logger = logging.getLogger("ingest_handbook")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a handbook version.")
    parser.add_argument("path", type=Path, help="handbook Markdown file")
    parser.add_argument("--version-label", required=True, help='e.g. "1.0"')
    parser.add_argument("--effective-date", required=True, type=date.fromisoformat)
    parser.add_argument("--publish", action="store_true", help="make this the published version")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    markdown = args.path.read_text(encoding="utf-8")

    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session, session.begin():
            result = ingest_handbook(
                session,
                markdown=markdown,
                source_filename=args.path.name,
                version_label=args.version_label,
                effective_date=args.effective_date,
                publish=args.publish,
            )
    except (HandbookAlreadyIngestedError, HandbookParseError) as exc:
        logger.error("%s", exc)
        return 1
    finally:
        engine.dispose()

    state = "published" if result.published else "stored as draft"
    logger.info("Handbook %s %s with %d sections.", result.version_label, state, result.chunk_count)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
