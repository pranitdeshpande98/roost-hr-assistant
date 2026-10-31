# ADR-0002: Modular monolith in Python with FastAPI

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | NFR-02, NFR-03, NFR-08, NFR-09, NFR-12 |

## Context

Roost serves ~4,500 employees (about 100 concurrent sessions, 2 requests/s
sustained). It is built and run by one developer, deployed on free-tier
hosting, and must stay easy to change as requirements evolve. The AI ecosystem
(LLM SDKs, agent frameworks, embeddings, evaluation tools) is strongest in Python.

## Options considered

### Architecture style

| Option | Pros | Cons |
|---|---|---|
| Microservices (agent, RAG, leave, auth as separate services) | Independent scaling and deploys | Network calls between services, distributed debugging, several deployments to run on free tiers; far more complexity than the load requires |
| Serverless functions | Scale to zero, pay per use | Cold starts break the 3 s first-token target; long-lived agent conversations and streaming fit poorly |
| **Modular monolith** | One deployable unit; simple to run, test and debug; modules can be extracted later | Requires discipline to keep module boundaries clean |

### Language and web framework

| Option | Pros | Cons |
|---|---|---|
| **Python + FastAPI** | Best AI/ML ecosystem; async for concurrent LLM calls; automatic OpenAPI docs; Pydantic validation | Slower raw CPU than Go/Java (irrelevant here: time is spent waiting on the LLM) |
| Python + Django | Batteries included (admin, ORM) | Heavier; async support less natural; we don't need server-rendered pages |
| TypeScript + Node | Good for web and streaming | Weaker for embeddings, evaluation and data tooling |

## Decision

Build Roost as a **single Python 3.12 service using FastAPI**, organized into
the modules under `src/roost/`. Module boundaries:

- `leave/` (business rules) has no dependency on `agent/`, `llm/` or `api/`.
  It is pure Python and can be tested without any AI.
- External systems (LLM, HR system, email, Teams) are reached only through
  interfaces in `llm/` and `adapters/`.
- `api/` is a thin HTTP layer; it contains no business logic.

## Consequences

- One container to build, deploy and monitor, which fits free-tier hosting.
- Async FastAPI handles the target concurrency because most time is spent
  waiting on LLM and database calls.
- Boundary rules must be enforced; we will add an automated import check in CI
  (Phase 10) so `leave/` can never import the LLM or agent code.
- If one module needs to scale independently later (e.g. ingestion), it can be
  extracted because it already sits behind a clear interface.
