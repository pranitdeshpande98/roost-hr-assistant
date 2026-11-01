# ADR-0010: Agent design: model-tool loop, deterministic rules and confirmation

- **Status:** Accepted
- **Date:** 2026-11-01
- **Phase:** 5 (core logic)
- **Builds on:** ADR-0003 (LangGraph), ADR-0006 (fixed templates), ADR-0009 (models)

## Context

Phase 5 turns the pieces chosen in Phases 3 and 4 (handbook retrieval, the model chain)
into Roost's conversation: answering handbook questions, and creating, changing and
cancelling vacation requests. Three risks shaped the design:

- **An LLM deciding policy.** A model can miscount days or misread "14 days' notice".
  HR rules must give the same answer every time and be testable.
- **An LLM inventing consent or outcomes.** "Your vacation is booked" when nothing was
  saved, or treating "yes, but change Friday" as approval (FR-10).
- **Safety text drifting.** The disclaimer (FR-04) and the binding-translation notice
  (FR-03) must appear on every reply, correctly translated (ADR-0006).

## Decision

### 1. A small LangGraph loop with injected dependencies

`call_model -> run_tools -> call_model ... -> finalize`. The model chooses tools; the
graph runs them and loops until the model answers, at most 3 tool rounds per turn (then
tools are withheld so it must answer). The model, tools, prompt, messages, checkpointer
and clock are all passed into `build_graph`, so every behaviour is tested with fakes and
one composition root (`build_agent`) wires the real parts.

### 2. Vacation rules are plain code, never the LLM

Rules V-01 to V-09 live in `roost.leave.rules` as pure functions over plain data (no
database, no model, no clock). Every rule runs and all failures are returned together,
each tied to its requirement ID and handbook section. A violation never carries a
balance (FR-06). The rules run again at save time, inside a per-team Postgres advisory
lock, so two people cannot take the last free spot (V-06) at the same moment.

### 3. Explicit confirmation through a graph pause, read by code

Tools that change data first check the rules (read-only), then call
`context.confirm(question)`, which pauses the graph with LangGraph's `interrupt()`. The
question comes from a fixed template. The employee's next message resumes the tool, and
plain code reads it: only a clear one-word yes (or sí, có) counts; anything else is
"unclear" and is passed back to the model, never treated as consent. On resume the tool
re-runs from the top, which is why nothing is changed before the question.

### 4. The model writes the answer; code writes the safety text

`finalize` composes every reply: the model's answer (or a fixed fallback if the model
chain is down), the binding notice for Spanish and Vietnamese, and the disclaimer. These
texts live in `config/messages.yaml`, validated at startup: every message in all three
languages, with identical placeholders.

### 5. Identity never comes from the model

Tool arguments are written by the model and can be manipulated. Who is asking comes from
`ToolContext` (set from the signed-in session), and every repository query filters by
that employee: another employee's request behaves exactly like one that does not exist.

### 6. Conversation memory in Postgres, in its own schema

LangGraph's `PostgresSaver` checkpoints state after every step, keyed by a random thread
id, so follow-up questions work and a pending confirmation survives a restart. Its
tables live in an `agent_memory` schema: Alembic creates the schema, LangGraph owns the
tables, and `alembic check` stays clean. Only an allowlist of types may be loaded from
checkpoints (loading arbitrary classes from storage is a code-execution risk). Only the
last 10 turns are sent to the model; the full history is kept.

## Consequences

### Positive

- Policy decisions are deterministic and covered by boundary tests (13 vs 14 days' notice,
  10 vs 11 days in a row, and so on).
- Nothing is saved without a clear yes, and a race for the last team spot cannot
  over-book a team.
- The disclaimer is on 100% of replies by construction, including outage fallbacks and
  confirmation questions.
- The agent works offline in tests: 378 tests, none of which call an LLM.

### Negative and risks

- Each conversation holds one Postgres connection in the terminal chat; the API
  (Phase 8) needs a connection pool.
- The rules engine treats every team the same; a team smaller than 7 would get 0 people
  off at a strict 15%, so at least one person is always allowed (business rules,
  decision 10).
- Confirmation words are a fixed list. Kiosk buttons (Phase 8) will send exact values;
  free-text replies outside the list are treated as unclear.
- Approvals by supervisors and HR, and the audit log, are not built yet (Phase 8). Until
  then requests stay pending.

### When to revisit

- A new language is added (confirmation words, templates and prompts).
- Approval workflows arrive and need their own graph or state machine.
- Usage shows the model regularly running out of tool rounds or history turns.
