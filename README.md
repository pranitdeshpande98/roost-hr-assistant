# Roost: Multilingual AI HR Assistant

> **Status:** ✅ Phase 2 complete · 🚧 Phase 3: data pipeline

Roost is a production-style AI assistant for **Hollow Creek Poultry Co.**, a
fictional poultry processor with three plants and a corporate office. It helps
~4,500 employees get HR answers and handle vacation requests without emailing HR.

> **Disclaimer:** Hollow Creek Poultry Co. is fictional. All policies, employees
> and data in this repository are synthetic.

## The problem

- Vacation requests go through a Power Apps form, followed by emails to HR.
- Employees email HR for answers already in a long, English-only handbook.
- Much of the plant workforce reads Spanish or Vietnamese first and works shifts
  when HR is closed. Drivers have no company email or Teams account.

## What Roost will do

- **Answer handbook questions** in English, Spanish and Vietnamese, citing the exact section
- **Handle vacation requests end-to-end:** rules checked by deterministic code,
  employee confirmation, then supervisor and HR approval
- **Route sensitive matters** (harassment, injury, safety) to the right HR team
  or the ethics hotline instead of answering them
- **Never reveal** balances, pay, or other financial data
- Run on **plant kiosks** (employee number + PIN) and **Microsoft Teams**

## Documentation

| Document | What it covers |
|---|---|
| [Business rules](docs/00-business-rules.md) | Approved HR rules the assistant follows |
| [Requirements](docs/01-requirements.md) | Functional and non-functional requirements with traceable IDs |
| [Architecture](docs/02-architecture.md) | System design, components and key flows |
| [Decision records](docs/adr/) | Why each major technology was chosen |

**Key architecture decisions:** modular monolith in Python/FastAPI · LangGraph
orchestration with Pydantic data contracts · OpenAI-compatible LLM providers
with fallback · local multilingual embeddings with pgvector hybrid search ·
PostgreSQL with an append-only audit log · server-rendered kiosk with
channel adapters for Teams.

## Tech stack (so far)

| Area | Tool |
|---|---|
| Language | Python 3.12 |
| Packaging and environments | [uv](https://docs.astral.sh/uv/) |
| Lint and format | Ruff |
| Type checking | mypy (strict) |
| Testing | pytest, pytest-cov |
| Git hooks | pre-commit |

## Getting started

**Prerequisites:** Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).
uv installs the correct Python version automatically.

```bash
git clone https://github.com/pranitdeshpande98/roost-hr-assistant.git
cd roost-hr-assistant
uv sync                       # creates .venv with Python 3.12 and all dev tools
uv run pre-commit install     # enables the git hooks
```

## Development commands

| Task | Command |
|---|---|
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type check | `uv run mypy` |
| Run tests | `uv run pytest` |
| Run all hooks | `uv run pre-commit run --all-files` |

## Project structure

```
src/roost/        application package
  core/           settings, shared types, errors
  api/            HTTP layer
  agent/          orchestration and prompts
  tools/          tools the agent can call
  leave/          vacation rules (deterministic, no LLM)
  rag/            handbook ingestion and retrieval
  llm/            provider-agnostic LLM client
  guardrails/     sensitive topics, PII, prompt injection, disclosure
  auth/           kiosk login and roles
  db/             data models and repositories
  adapters/       HR system, email, notifications (mocked)
  observability/  logging and tracing
tests/            unit and integration tests
evals/            evaluation datasets and runners
data/             handbook source and synthetic data
config/           YAML configuration
docs/             requirements and architecture decisions
```

## Roadmap

- [x] Phase 1: Project foundation and tooling
- [x] Phase 2: Requirements and architecture
- [ ] Phase 3: Data pipeline (handbook ingestion)
- [ ] Phase 4: Model selection
- [ ] Phase 5: Core logic (RAG and vacation agent)
- [ ] Phase 6: Evaluations
- [ ] Phase 7: Guardrails
- [ ] Phase 8: API layer
- [ ] Phase 9: Observability
- [ ] Phase 10: Containers and CI/CD
- [ ] Phase 11: Deployment
- [ ] Phase 12: Teams integration and post-launch
