"""Run the LLM judge: calibrate it on hand-labelled examples, then judge agent results.

The judge is a different model family from the agent (Qwen judges gpt-oss answers), so
it is not grading its own work. Judgments are saved per turn; the run resumes after a
rate-limit stop (exit code 3).

Usage:
    uv run python scripts/judge_agent.py --calibrate   # must pass before trusting it
    uv run python scripts/judge_agent.py               # judge saved agent results
    uv run python scripts/judge_agent.py --rejudge     # discard saved judgments first
    uv run python scripts/eval_agent.py --report       # gates now include the judge
"""

import argparse
import logging
import sys
import time
from functools import partial
from pathlib import Path

from roost.agent.messages import default_messages
from roost.core.enums import Language
from roost.evals.agent_dataset import EVAL_TODAY
from roost.evals.agent_harness import load_case_records
from roost.evals.agent_scoring import answer_text
from roost.evals.judge import (
    Judgment,
    agreement,
    append_judgment,
    date_context,
    judge_answer,
    judgeable_turns,
    judgments_path,
    load_calibration,
    load_judgments,
    remove_judgments,
)
from roost.llm.client import OpenAICompatibleChatModel
from roost.llm.errors import LLMRequestError, LLMResponseError, LLMUnavailableError
from roost.llm.providers import parse_model_ref
from roost.llm.types import ChatModel

logger = logging.getLogger("judge_agent")

DEFAULT_JUDGE = "groq:qwen/qwen3.8-27b"  # a different family from the agent's primary model
CALIBRATION = Path("evals/datasets/judge_calibration_v1.yaml")
RESULTS_DIR = Path("evals/results/agent_v1")
# The judge is trusted only if it agrees with the human labels this often...
MIN_AGREEMENT = 0.90
# ...and catches every answer a human marked unfaithful.
MIN_CAUGHT_UNFAITHFUL = 1.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate the LLM judge or judge results.")
    parser.add_argument("--judge", default=DEFAULT_JUDGE, help="provider:model of the judge")
    parser.add_argument("--calibrate", action="store_true", help="run the calibration set only")
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between judge calls")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--rejudge",
        action="store_true",
        help="discard saved judgments and judge again (after changing the judge)",
    )
    parser.add_argument(
        "--scenario", action="append", help="with --rejudge: only this scenario id (repeatable)"
    )
    return parser.parse_args(argv)


def calibrate(judge: ChatModel, delay: float, out: Path) -> int:
    items = load_calibration(CALIBRATION)
    verdicts = []
    for index, item in enumerate(items):
        if index:
            time.sleep(delay)
        verdicts.append(judge_answer(judge, item.answer, item.sources, item.context))
        logger.info("  %-30s judged", item.id)
    result = agreement(items, verdicts)
    trusted = (
        result.faithful_agreement >= MIN_AGREEMENT
        and result.citation_agreement >= MIN_AGREEMENT
        and result.caught_unfaithful >= MIN_CAUGHT_UNFAITHFUL
    )
    lines = [
        f"# Judge calibration: {judge.name}",
        "",
        "| Measure | Result | Required |",
        "|---|---|---|",
        f"| Faithfulness agreement | {result.faithful_agreement:.0%} | {MIN_AGREEMENT:.0%} |",
        f"| Citation agreement | {result.citation_agreement:.0%} | {MIN_AGREEMENT:.0%} |",
        f"| Unfaithful answers caught | {result.caught_unfaithful:.0%} | {MIN_CAUGHT_UNFAITHFUL:.0%} |",
        "",
        f"**{'TRUSTED' if trusted else 'NOT TRUSTED'}** on {result.items} hand-labelled examples.",
        "",
        "## Disagreements",
        "",
        *([f"- {d}" for d in result.disagreements] or ["None."]),
    ]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    out.mkdir(parents=True, exist_ok=True)
    (out / "judge_calibration.md").write_text(text, encoding="utf-8", newline="\n")
    return 0 if trusted else 1


def rejudge(out: Path, scenarios: list[str] | None) -> None:
    """Discard saved judgments (all, or of the given scenarios' cases)."""
    for results in sorted(out.glob("*.jsonl")):
        case_ids = (
            {r.case_id for r in load_case_records(results) if r.scenario_id in set(scenarios)}
            if scenarios
            else None
        )
        removed = remove_judgments(judgments_path(out, results.stem), case_ids)
        logger.info("%s: discarded %d judgments", results.stem, removed)


def judge_results(judge: ChatModel, delay: float, out: Path) -> int:
    messages = default_messages()
    context = date_context(EVAL_TODAY)  # what the agent was told
    for results in sorted(out.glob("*.jsonl")):
        path = judgments_path(out, results.stem)
        done = {(j.case_id, j.turn) for j in load_judgments(path)}
        todo: list[tuple[str, int, str, list[str]]] = []
        for record in load_case_records(results):
            strip = partial(answer_text, language=Language(record.language), messages=messages)
            for number, answer, sources in judgeable_turns(record, strip):
                if (record.case_id, number) not in done:
                    todo.append((record.case_id, number, answer, sources))
        logger.info("%s: %d turns to judge (%d done)", results.stem, len(todo), len(done))
        for index, (case_id, number, answer, sources) in enumerate(todo, start=1):
            if index > 1:
                time.sleep(delay)
            try:
                verdict = judge_answer(judge, answer, sources, context)
                judgment = Judgment(case_id=case_id, turn=number, judge=judge.name, verdict=verdict)
            except LLMResponseError as error:
                judgment = Judgment(
                    case_id=case_id, turn=number, judge=judge.name, error=str(error)
                )
            except LLMUnavailableError as error:
                logger.warning("Stopped at %s: %s. Re-run the same command later.", case_id, error)
                return 3
            append_judgment(path, judgment)
            logger.info("  [%d/%d] %s turn %d", index, len(todo), case_id, number)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        judge = OpenAICompatibleChatModel.create(*parse_model_ref(args.judge))
    except LLMRequestError as error:
        logger.error("FAILED: %s", error)
        return 1
    try:
        if args.calibrate:
            return calibrate(judge, args.delay, args.out)
        if args.rejudge:
            rejudge(args.out, args.scenario)
        return judge_results(judge, args.delay, args.out)
    except LLMUnavailableError as error:
        logger.warning("Judge unavailable: %s. Re-run the same command later.", error)
        return 3


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
