"""Evaluate the input guardrail on the labelled EN/ES/VI set, resumably.

Tune on dev, report on test: change prompts, keywords or thresholds while looking only
at --split dev, then run --split test once for the reported numbers.

Usage:
    uv run python scripts/eval_guardrails.py --split dev            # keywords + LLM
    uv run python scripts/eval_guardrails.py --split dev --no-llm   # keywords + fallback
    uv run python scripts/eval_guardrails.py --split dev --rerun    # after a change
    uv run python scripts/eval_guardrails.py --report               # no API calls
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from roost.core.enums import Language
from roost.evals.agent_harness import ModelOutageError, RecordingChatModel, results_path
from roost.evals.guardrail_dataset import expand_guardrail_cases, load_guardrail_items
from roost.evals.guardrail_eval import (
    GuardrailRecord,
    append_guardrail_record,
    format_guardrail_report,
    gates_pass,
    load_guardrail_records,
    remove_guardrail_records,
)
from roost.guardrails.classifier import Source
from roost.guardrails.factory import build_input_guardrail
from roost.llm.client import build_chat_model
from roost.llm.errors import LLMRequestError
from roost.llm.providers import get_llm_settings
from roost.rag.embeddings import EMBEDDING_MODELS, SentenceTransformerEmbedder
from roost.tools.handbook import HANDBOOK_EMBEDDING_MODEL

logger = logging.getLogger("eval_guardrails")

DATASET = Path("evals/datasets/guardrails_v2.yaml")
RESULTS_DIR = Path("evals/results/guardrails_v2")
LOCAL_ONLY = "local-only (keywords + similarity)"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the input guardrail.")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    parser.add_argument("--language", action="append", choices=[lang.value for lang in Language])
    parser.add_argument("--no-llm", action="store_true", help="keywords and fallback only")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds after each LLM call")
    parser.add_argument("--rerun", action="store_true", help="discard the selected saved results")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument("--report", action="store_true", help="score saved results only")
    return parser.parse_args(argv)


def report(out: Path) -> int:
    """Every results file, dev and test separately. Exit 1 if a gate fails on test (or
    on dev when there is no test run yet)."""
    sections: list[str] = []
    ok = True
    for path in sorted(out.glob("*.jsonl")):
        records = load_guardrail_records(path)
        splits = [s for s in ("dev", "test") if any(r.split == s for r in records)]
        sections.append(f"# Guardrail eval: {path.stem}\n")
        for split in splits:
            chosen = [r for r in records if r.split == split]
            sections.append(format_guardrail_report(chosen, f"{split} split"))
        gated = "test" if "test" in splits else "dev"
        ok = ok and gates_pass([r for r in records if r.split == gated])
    if not sections:
        logger.error("No results in %s yet.", out)
        return 1
    text = "\n".join(sections)
    sys.stdout.write(text)
    (out / "summary.md").write_text(text, encoding="utf-8", newline="\n")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.report:
        return report(args.out)

    split = None if args.split == "all" else args.split
    cases = expand_guardrail_cases(load_guardrail_items(args.dataset), split)
    if args.language:
        cases = [c for c in cases if c.language.value in set(args.language)]

    model: RecordingChatModel | None = None
    if not args.no_llm:
        try:
            model = RecordingChatModel(build_chat_model(get_llm_settings().guardrail_model_refs))
        except LLMRequestError as error:
            logger.error("FAILED: %s", error)
            return 1
    path = results_path(args.out, model.name if model else LOCAL_ONLY)
    if args.rerun:
        removed = remove_guardrail_records(path, (c.case_id for c in cases))
        logger.info("Discarded %d saved results to run again", removed)
    done = {r.case_id for r in load_guardrail_records(path)}
    todo = [c for c in cases if c.case_id not in done]
    logger.info("%s: %d to run (%d already done)", path.stem, len(todo), len(cases) - len(todo))
    if not todo:
        return 0

    embedder = SentenceTransformerEmbedder(EMBEDDING_MODELS[HANDBOOK_EMBEDDING_MODEL])
    guardrail = build_input_guardrail(embedder, model)
    for index, case in enumerate(todo, start=1):
        started = time.perf_counter()
        decision = guardrail.classify(case.text)
        latency = (time.perf_counter() - started) * 1000
        if model is not None:
            try:
                model.take()  # the guardrail falls back on an outage; the eval must not
            except ModelOutageError as error:
                logger.warning(
                    "Stopped at %s: %s. Re-run the same command later.", case.case_id, error
                )
                return 3
        append_guardrail_record(
            path,
            GuardrailRecord(
                case_id=case.case_id,
                split=case.item.split,
                language=case.language,
                tag=case.item.tag,
                text=case.text,
                expected=case.item.category,
                predicted=decision.category,
                source=decision.source,
                reason=decision.reason,
                latency_ms=latency,
            ),
        )
        mark = "ok" if decision.route is case.item.route else "WRONG"
        logger.info(
            "  [%d/%d] %-34s %-9s %-15s %s",
            index,
            len(todo),
            case.case_id,
            decision.source.value,
            decision.category.value,
            mark,
        )
        if decision.source is Source.LLM:
            time.sleep(args.delay)
    logger.info("Done. Results: %s", path)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
