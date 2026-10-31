# ADR-0003: LangGraph for orchestration, Pydantic for data contracts

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | FR-01, FR-10, FR-13, FR-30, NFR-09, NFR-10, NFR-11 |

## Context

The agent must call tools (handbook search, vacation tools, payroll schedule,
HR case summary), pause for explicit employee confirmation before submitting
a request, keep conversation state across turns and sessions, run guardrails
in a fixed order, and work with multiple LLM providers on free tiers.
Data passed between the LLM, tools and business rules must be validated,
because LLM output is untrusted.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| Plain LLM function calling (no framework) | No dependency; full control | We would hand-build state persistence, interrupts, retries and streaming |
| **LangGraph + Pydantic models** | Explicit graph control flow; built-in human-in-the-loop interrupts; checkpointing; provider-agnostic; every data boundary validated by Pydantic | Learning curve; LangChain ecosystem has historically changed APIs often |
| LangGraph + Pydantic AI agents inside nodes | Pydantic AI's typed tool loop inside each node | Two agent loops and two tool-calling models to learn and debug; no node needs its own tool loop |
| Pydantic AI alone | Excellent type safety; simple code | Less built-in support for durable, multi-step flows with pauses |
| CrewAI | Fast multi-agent prototyping | Role-based multi-agent design we do not need; less control over flow |

## Decision

Use **LangGraph** to orchestrate the agent as an explicit graph:
input guardrails → agent → tools → confirmation interrupt → submit → output guardrails.

Use **Pydantic models for every data contract**:

- **Graph state:** the conversation state passed between nodes is a typed model.
- **Tool inputs and outputs:** each tool in `src/roost/tools/` takes and returns
  Pydantic models. LLM-proposed arguments are validated before a tool runs;
  invalid arguments are rejected, never "best-effort" used.
- **Structured LLM outputs:** classifiers and extractors (e.g. sensitive-topic
  detection, date extraction) must return a Pydantic model, not free text.
- **API layer:** FastAPI request and response bodies use the same models.

To limit framework lock-in:

- Tools are plain, typed Python functions and do not import LangGraph.
- Business rules stay in `src/roost/leave/`, independent of any framework (ADR-0002).
- LLM calls go through our own `src/roost/llm/` interface (ADR-0004).

## Consequences

- The confirmation step (FR-10) is enforced by the graph, not by prompting.
- Malformed or malicious LLM output fails validation at the boundary instead of
  reaching business rules or the database.
- Conversation state is checkpointed to the relational database (ADR-0007),
  so a kiosk session can resume and conversations can be purged after 45 days (FR-45).
- The graph makes traces readable node by node (NFR-11).
- If LangGraph's API changes, only `src/roost/agent/` is affected; tools,
  models and rules are untouched.
- The Teams channel (P1) connects through a thin adapter in front of this
  agent; it does not change the agent framework (ADR-0008).
- We pin LangGraph and Pydantic in `uv.lock` and upgrade deliberately.
