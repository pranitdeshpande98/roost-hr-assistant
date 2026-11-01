"""Embed the published handbook with one or more candidate models.

Usage:
    uv run python scripts/embed_handbook.py --model bge-m3
    uv run python scripts/embed_handbook.py --all
    uv run python scripts/embed_handbook.py --all --include-gated   # needs HF_TOKEN
"""

import argparse
import logging
import sys
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.rag.embed_store import NoPublishedHandbookError, embed_published_handbook
from roost.rag.embeddings import EMBEDDING_MODELS, SentenceTransformerEmbedder

logger = logging.getLogger("embed_handbook")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed the published handbook.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--model", choices=sorted(EMBEDDING_MODELS), help="one model")
    target.add_argument("--all", action="store_true", help="every candidate model")
    parser.add_argument(
        "--include-gated",
        action="store_true",
        help="with --all, also run gated models (needs a Hugging Face token)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.model:
        specs = [EMBEDDING_MODELS[args.model]]
    else:
        specs = [s for s in EMBEDDING_MODELS.values() if args.include_gated or not s.gated]

    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        for spec in specs:
            started = time.perf_counter()
            embedder = SentenceTransformerEmbedder(spec)
            loaded = time.perf_counter()
            with Session(engine) as session, session.begin():
                count = embed_published_handbook(session, embedder)
            logger.info(
                "%-32s embedded %3d chunks  (load %.1fs, embed %.1fs)",
                spec.key,
                count,
                loaded - started,
                time.perf_counter() - loaded,
            )
    except NoPublishedHandbookError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
