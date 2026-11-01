"""Run the LLM eval for one model, resumably, then print the comparison table.

Retrieval is fixed to the eval winner (bge-m3, vector search, top 3) so only the LLM varies.
Each case is saved as soon as it finishes; re-running the same command resumes.

Usage:
    uv run python scripts/eval_llm.py --provider groq --model openai/gpt-oss-120b
    uv run python scripts/eval_llm.py --provider openrouter --model <id>:free --intents 5
    uv run python scripts/eval_llm.py --report
"""

import argparse
import logging
import re
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from roost.core.config import get_settings
from roost.evals.dataset import RetrievalCase, load_retrieval_cases
from roost.evals.llm_eval import (
    append_record,
    format_llm_report,
    load_records,
    run_case,
    score_record,
)
from roost.llm.client import OpenAICompatibleChatModel
from roost.llm.errors import LLMRequestError, LLMUnavailableError
from roost.llm.providers import PROVIDERS
from roost.rag.embeddings import EMBEDDING_MODELS, SentenceTransformerEmbedder
from roost.rag.retrieval import SectionText, get_sections, vector_search

logger = logging.getLogger("eval_llm")

DATASET = Path("evals/datasets/retrieval_v1.yaml")
RESULTS_DIR = Path("evals/results/llm_v1")
RETRIEVAL_MODEL = "bge-m3"
TOP_K = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate an LLM on the HR question set.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS))
    parser.add_argument("--model", help="model id on that provider")
    parser.add_argument(
        "--intents", type=int, help="only the first N intents (x3 languages); default: all"
    )
    parser.add_argument(
        "--delay", type=float, default=10.0, help="seconds between cases, to respect rate limits"
    )
    parser.add_argument("--report", action="store_true", help="only print the comparison table")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    if not args.report and not (args.provider and args.model):
        parser.error("--provider and --model are required unless --report is given")
    return args


def results_file(out: Path, model_name: str) -> Path:
    """One JSONL file per model, e.g. groq__openai_gpt-oss-120b.jsonl."""
    return out / (re.sub(r"[^A-Za-z0-9._-]+", "_", model_name.replace("/", "__", 1)) + ".jsonl")


def report(out: Path) -> str:
    scores = [score_record(r) for path in sorted(out.glob("*.jsonl")) for r in load_records(path)]
    return format_llm_report(scores) if scores else "No results yet.\n"


def select_cases(cases: list[RetrievalCase], intents: int | None) -> list[RetrievalCase]:
    if intents is None:
        return cases
    keep = list(dict.fromkeys(c.intent for c in cases))[:intents]
    return [c for c in cases if c.intent in keep]


def run(args: argparse.Namespace) -> int:
    model = OpenAICompatibleChatModel.create(args.provider, args.model)
    path = results_file(args.out, model.name)
    done = {r.case_id for r in load_records(path)}
    cases = select_cases(load_retrieval_cases(args.dataset), args.intents)
    todo = [c for c in cases if c.case_id not in done]
    logger.info(
        "%s: %d cases, %d already done, %d to run",
        model.name,
        len(cases),
        len(cases) - len(todo),
        len(todo),
    )
    if not todo:
        return 0

    embedder = SentenceTransformerEmbedder(EMBEDDING_MODELS[RETRIEVAL_MODEL])
    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        with Session(engine) as session:

            def search(query: str) -> list[SectionText]:
                vector = embedder.embed_queries([query])[0]
                numbers = vector_search(session, RETRIEVAL_MODEL, vector, limit=TOP_K)
                return get_sections(session, numbers)

            for index, case in enumerate(todo, start=1):
                if index > 1:
                    time.sleep(args.delay)
                try:
                    record = run_case(model, case, search)
                except LLMUnavailableError as exc:
                    logger.warning(
                        "Stopped at %s: %s. Progress is saved; re-run the same command later.",
                        case.case_id,
                        exc,
                    )
                    return 3
                append_record(path, record)
                status = "ERROR " + record.error if record.error else "ok"
                logger.info(
                    "  [%d/%d] %-28s %6.0f ms  %s",
                    index,
                    len(todo),
                    case.case_id,
                    record.latency_ms,
                    status,
                )
    finally:
        engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.report:
        try:
            code = run(args)
        except LLMRequestError as exc:
            logger.error("FAILED: %s", exc)
            return 1
        if code:
            return code
    table = report(args.out)
    sys.stdout.write(table)
    (args.out / "summary.md").parent.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.md").write_text(table, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
