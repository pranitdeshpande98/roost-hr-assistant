# ADR-0011: Whole-agent evaluation: scenarios, deterministic checks and a calibrated judge

- **Status:** Accepted
- **Date:** 2026-11-03
- **Phase:** 6 (evaluations)
- **Builds on:** ADR-0009 (model selection), ADR-0010 (agent design)

## Context

ADR-0009 evaluated the pieces: retrieval on its own, and single model calls. Phase 6
asks whether the assembled agent meets the quality gates in requirements section 6.2:
the real graph, real tools, real database and real model chain, across multi-turn
conversations in English, Spanish and Vietnamese. Three constraints shaped the design:

- **Evals must not change data.** Vacation scenarios book real requests.
- **Free-tier quotas.** gpt-oss-120b allows about 200k tokens a day on Groq, and one full
  run uses about 178k. A run must survive a quota stop and resume.
- **Two gates need judgment.** Faithfulness and citation correctness cannot be decided
  by string matching, and an LLM judge is only useful if it is shown to agree with a human.

## Decision

### 1. Scenarios written once, run in every language

`evals/datasets/agent_v1.yaml` holds 26 scenarios (handbook 9, vacation 6, restricted 5,
payday 2, out of scope 2, multi-turn 2), each with the employee's words in all three
languages and per-turn expectations: which tools, which vacation days, how the tool
result starts, whether the agent pauses for confirmation, which sections it cites, which
facts it mentions, and whether the question asks for restricted data. That gives 78
cases and 90 turns. The clock is pinned to Monday 2026-11-02 so "next Monday" has one
right answer.

### 2. The real agent, in a rolled-back transaction

`scripts/eval_agent.py` builds the production agent with an in-memory checkpointer and
runs each case inside one database transaction that is always rolled back (tools'
commits only release savepoints). A seeded employee is chosen by profile (office
biweekly or production weekly) with enough vacation hours, past one year of service and
no open requests, so a vacation scenario fails only for the rule it tests. Results are
appended per case to JSONL; a quota stop exits with code 3 and the same command resumes.
`--rerun` discards selected cases to run them again.

A wrapper around the model records every call. The production graph turns a full outage
into a polite fallback reply; the eval stops instead of scoring that reply. Each turn
records which model answered, so a run that silently fell back to another model is
visible (the final run: 144 of 145 calls on gpt-oss-120b, 1 on the fallback).

### 3. Deterministic checks wherever possible

Tools, arguments, results, pauses, expected citations and mentions, the disclaimer, the
reply language, citations to sections no tool returned, and restricted-data leaks are
all decided by code (`roost.evals.agent_scoring`). The leak check is deliberately
strict: any digit in the answer to a restricted question fails, except section
citations and the plan name "401(k)". A figure the agent quotes from the handbook in
place of the employee's own data fails too, because an employee can mistake it for theirs.

### 4. An LLM judge from another model family, calibrated first

Qwen (qwen3.8-27b) judges gpt-oss answers, so the agent's model does not grade its own
work. The judge sees what the agent saw: the tool calls with their arguments and
results, and today's date. Before it is trusted it must agree with 23 hand-labelled
examples (correct answers, true claims with the wrong section, wrong facts with the right
section, both wrong, and correct and wrong date calculations): at least 90% agreement on
both verdicts and every unfaithful example caught. Result: 100% faithfulness agreement,
96% citation agreement, 100% of unfaithful answers caught.

### 5. One report, one exit code

`eval_agent.py --report` scores saved results with the saved judgments and writes
`evals/results/agent_v1/summary.md`: the gates, every check by language, pass rates by
category and every failure with its detail. It exits 1 if any gate fails, so Phase 10
can block a merge on it.

## Results

Four runs, each against all 78 cases. Scorer fixes and agent fixes were committed and
measured separately, so each improvement can be attributed.

| Gate (threshold) | Run 1 | Run 1, scorer fixed | Run 2 | Run 3 (final) |
|---|---|---|---|---|
| Tool-call correctness (95%) | 91% FAIL | 92% FAIL | 100% | 99% |
| Restricted disclosure (0 cases) | 80% FAIL | 100% | 80% FAIL | **87% FAIL** |
| Disclaimer present (100%) | 100% | 100% | 100% | 100% |
| Reply language (98%) | 100% | 100% | 100% | 100% |
| Faithfulness, LLM judge (90%) | 88% FAIL | 94% | 94% | 94% |
| Citation correctness, LLM judge (90%) | 96% | 97% | 99% | 97% |
| Failed checks listed | 48 | 21 | 10 | 11 |

Final run: 90 turns, about 178k tokens, median 3.0 s per turn.

### What the first run got wrong about itself (scorer fixes)

Half of the first run's failures were measurement errors, found by reading transcripts
rather than trusting the numbers:

- "401(k)" counted as a disclosed figure (3 false leaks).
- Sections cited by the pay-schedule and vacation tools did not count as retrieved
  (18 false invalid citations).
- The reference code was given, but with a non-breaking hyphen (`VR‑NJ8NXW`).
- The judge did not see today's date or the tool arguments, so "next Monday is 7 days
  away" and the booked dates looked unsupported.
- An extra read-only search before booking failed the tools check.
- A short Spanish refusal was detected as English ("a" was an English marker).

### What the agent got wrong (agent fixes)

- **Booking in Spanish and Vietnamese.** Two prompt rules conflicted: "search the
  handbook first for leave questions" and "use the vacation tools". The model sometimes
  searched and answered from the handbook instead of booking. Rule 1 now defers to the
  vacation rule; tool-call correctness went from 92% to 100%.
- **Saying "no".** After a Vietnamese "không" the agent asked for confirmation again.
  The tool's result now tells it to say nothing was sent and not to ask again.
- **Weekdays.** The agent called Saturday 2026-12-12 a Sunday. Rule failures now state
  each date's weekday, so the model no longer works it out.
- **Invented citations and conditions.** The prompt now limits citations to sections
  that appear in tool results and forbids adding unstated conditions.

## Consequences

### Positive

- Five of six Phase 6 gates pass on the final run, in all three languages.
- Every change was measured on the full set against a committed baseline, so a prompt
  change cannot quietly break another scenario.
- The judge is a measured instrument, recalibrated whenever its prompt changes.

### Negative and risks

- **The restricted-disclosure gate fails (13/15).** Across three runs the agent
  answered 2 to 3 of 15 own-data questions with handbook figures or, once, an invented
  statement ("you are enrolled in medical, dental and vision"). A prompt rule lowers how
  often this happens but cannot make it zero, which is what FR-06 requires. **Phase 7
  owns this**: a deterministic guardrail that recognises own-data questions before the
  model runs and replies with a fixed, translated referral to HR. Phase 7 is accepted
  only when this gate reaches 100%.
- **Run-to-run variance.** The same prompt gave 12, 12 and 13 of 15 restricted passes
  and different failing cases each time. One run is a sample; gates near their
  threshold need repeated runs before a decision.
- **Judge blind spots.** Qwen marked "27 de noviembre" unsupported in three runs although
  the tool listed it. Known judge noise is recorded, not tuned away.
- **Cost.** A full run uses about 90% of gpt-oss-120b's free daily tokens, so full runs
  are limited to one a day. CI (Phase 10) will run a smaller smoke set on every merge.

### When to revisit

- The restricted guardrail lands (Phase 7): re-run, and expect 100%.
- The agent's model changes: re-run everything and recalibrate the judge if its model
  changes.
- Scenarios are added for sensitive-topic routing and prompt injection (Phase 7 gates).
