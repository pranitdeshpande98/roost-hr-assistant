"""Measure retrieval quality: keyword vs vector vs hybrid, per model and language.

Needs the handbook ingested, published and embedded (scripts/embed_handbook.py).

Usage:
    uv run python scripts/eval_retrieval.py                  # every embedded model
    uv run python scripts/eval_retrieval.py --model bge-m3
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.db.models import ChunkEmbedding
from roost.evals.dataset import load_retrieval_cases
from roost.evals.retrieval_eval import CaseOutcome, format_markdown, score_case, summarize
from roost.rag.embeddings import EMBEDDING_MODELS, SentenceTransformerEmbedder
from roost.rag.retrieval import hybrid_search, keyword_search, vector_search

logger = logging.getLogger("eval_retrieval")

DATASET = Path("evals/datasets/retrieval_v1.yaml")
RESULTS_DIR = Path("evals/results")
TOP_K = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate handbook retrieval.")
    parser.add_argument(
        "--model",
        action="append",
        choices=sorted(EMBEDDING_MODELS),
        help="model to evaluate (repeatable); default: every model already embedded",
    )
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_retrieval_cases(args.dataset)
    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    outcomes: list[CaseOutcome] = []
    try:
        with Session(engine) as session:
            embedded = set(session.scalars(select(ChunkEmbedding.model_name).distinct()))
            models = args.model or sorted(embedded)
            missing = [m for m in models if m not in embedded]
            if missing or not models:
                logger.error("Not embedded yet: %s. Run embed_handbook.py first.", missing)
                return 1

            # Keyword search does not depend on the embedding model: run it once.
            for case in cases:
                ranked = keyword_search(session, case.question, limit=TOP_K)
                outcomes.append(score_case(case, "keyword", ranked))

            for model in models:
                logger.info("Evaluating %s on %d questions ...", model, len(cases))
                embedder = SentenceTransformerEmbedder(EMBEDDING_MODELS[model])
                vectors = embedder.embed_queries([c.question for c in cases])
                for case, vector in zip(cases, vectors, strict=True):
                    ranked = vector_search(session, model, vector, limit=TOP_K)
                    outcomes.append(score_case(case, f"vector:{model}", ranked))
                    ranked = hybrid_search(session, model, case.question, vector, limit=TOP_K)
                    outcomes.append(score_case(case, f"hybrid:{model}", ranked))
    finally:
        engine.dispose()

    table = format_markdown(summarize(outcomes, k=TOP_K), k=TOP_K)
    sys.stdout.write(table)

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.dataset.stem
    (args.out / f"{stem}.md").write_text(table, encoding="utf-8", newline="\n")
    per_case = [asdict(o) for o in outcomes]
    (args.out / f"{stem}_cases.json").write_text(
        json.dumps(per_case, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    logger.info("Wrote %s/%s.md and %s_cases.json", args.out, stem, stem)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
