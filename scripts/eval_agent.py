"""Run the agent eval set through the real agent, resumably.

Every case runs in a rolled-back transaction on your database, with the clock pinned to
2026-11-02 and a seeded employee signed in. Results are saved per case; if the model
chain runs out of quota the run stops (exit code 3) and the same command resumes.

Usage:
    uv run python scripts/eval_agent.py
    uv run python scripts/eval_agent.py --scenario payday-weekly --language en
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine

from roost.agent.factory import build_agent
from roost.core.config import get_settings
from roost.core.enums import Language
from roost.evals.agent_dataset import EVAL_TODAY, expand_cases, load_agent_scenarios
from roost.evals.agent_harness import (
    Agent,
    ModelOutageError,
    RecordingChatModel,
    Sessions,
    append_case_record,
    load_case_records,
    pick_employee,
    results_path,
    rolled_back_sessions,
    run_case,
)
from roost.llm.client import build_chat_model
from roost.llm.errors import LLMRequestError
from roost.llm.providers import get_llm_settings
from roost.rag.embeddings import EMBEDDING_MODELS, SentenceTransformerEmbedder
from roost.tools.handbook import HANDBOOK_EMBEDDING_MODEL

logger = logging.getLogger("eval_agent")

DATASET = Path("evals/datasets/agent_v1.yaml")
RESULTS_DIR = Path("evals/results/agent_v1")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the whole agent.")
    parser.add_argument("--scenario", action="append", help="only this scenario id (repeatable)")
    parser.add_argument("--language", action="append", choices=[lang.value for lang in Language])
    parser.add_argument("--delay", type=float, default=10.0, help="seconds between cases")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = expand_cases(load_agent_scenarios(args.dataset))
    if args.scenario:
        cases = [c for c in cases if c.scenario.id in set(args.scenario)]
    if args.language:
        cases = [c for c in cases if c.language.value in set(args.language)]

    try:
        model = RecordingChatModel(build_chat_model(get_llm_settings().agent_model_refs))
    except LLMRequestError as error:
        logger.error("FAILED: %s", error)
        return 1
    path = results_path(args.out, model.name)
    done = {r.case_id for r in load_case_records(path)}
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

    embedder = SentenceTransformerEmbedder(EMBEDDING_MODELS[HANDBOOK_EMBEDDING_MODEL])

    def make_agent(sessions: Sessions) -> Agent:
        return build_agent(
            sessions,
            model=model,
            embedder=embedder,
            checkpointer=InMemorySaver(),  # memory per case; nothing written to Postgres
            clock=lambda: EVAL_TODAY,
        )

    engine = create_engine(get_settings().resolved_database_url.get_secret_value())
    try:
        for index, case in enumerate(todo, start=1):
            if index > 1:
                time.sleep(args.delay)
            with rolled_back_sessions(engine) as sessions:
                with sessions() as session:
                    employee = pick_employee(session, case.scenario.employee, EVAL_TODAY)
                try:
                    record = run_case(case, make_agent, model, sessions, employee)
                except ModelOutageError as error:
                    logger.warning(
                        "Stopped at %s: %s. Progress is saved; re-run the same command later.",
                        case.case_id,
                        error,
                    )
                    return 3
            append_case_record(path, record)
            logger.info(
                "  [%d/%d] %-34s %6.0f ms", index, len(todo), case.case_id, record.latency_ms
            )
    finally:
        engine.dispose()
    logger.info("Done. Results: %s", path)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
