# Roost: Multilingual AI HR Assistant

> **Status:** ✅ Phase 5 complete · 🚧 Phase 6: evaluations

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
| [Data model](docs/03-data-model.md) | Database tables and how they relate |
| [Decision records](docs/adr/) | Why each major technology was chosen |

**Key architecture decisions:** modular monolith in Python/FastAPI · LangGraph
orchestration with Pydantic data contracts · OpenAI-compatible LLM providers
with a fallback chain · local multilingual embeddings (bge-m3) with pgvector
vector search · PostgreSQL with an append-only audit log · server-rendered kiosk
with channel adapters for Teams.

## How the agent works (Phase 5)

```text
employee message ─► call_model ──(tool call?)──► run_tools ──► call_model ...
                        │ no                        │
                        ▼                           └─ vacation change? check rules ─► PAUSE: "Please confirm…"
                    finalize ─► answer + translation notice + disclaimer      employee replies YES ─► save
```

- **Handbook answers** come only from `search_handbook` (bge-m3 retrieval), with `[§x.y]` citations.
- **Vacation rules V-01 to V-09** run in plain Python, never in the LLM, and are re-checked at save
  time under a per-team lock so a team cannot be over-booked.
- **Nothing is saved without a clear "yes"**: the graph pauses, and code (not the model) reads the reply.
- **The disclaimer and translation notice** come from reviewed templates in `config/messages.yaml`.
- **Conversations are saved in Postgres**, so follow-up questions work and a pending confirmation
  survives a restart. Details and trade-offs: [ADR-0010](docs/adr/0010-agent-design.md).

## Model selection (Phase 4)

Chosen by evaluation, not leaderboards: 90 questions (30 intents x English, Spanish, Vietnamese).
Full evidence and reasoning: [ADR-0009](docs/adr/0009-model-selection.md).

| Component | Choice | Key result |
|---|---|---|
| Embeddings | `BAAI/bge-m3` (local, CPU) | 98% hit@5 overall (English 100%, Spanish 100%, Vietnamese 93%) |
| Retrieval | Vector search, top 3 | 98% vs 90% for hybrid keyword + vector search |
| Agent LLM | `gpt-oss-120b` → `qwen3.8-27b` → `nemotron-3-ultra` (free) | 100% tool use, language match and valid citations; ~1 s median |

Reproduce: `uv run python scripts/eval_retrieval.py` and `uv run python scripts/eval_llm.py --report`.

## Tech stack (so far)

| Area | Tool |
|---|---|
| Language | Python 3.12 |
| Packaging and environments | [uv](https://docs.astral.sh/uv/) |
| Database | PostgreSQL 17 + pgvector (Docker) |
| ORM and migrations | SQLAlchemy 2.1, Alembic |
| Configuration | pydantic-settings |
| Agent orchestration | LangGraph (Postgres checkpointer) |
| Embeddings | sentence-transformers (`BAAI/bge-m3`, CPU) |
| LLM providers | Groq and OpenRouter free tiers, via the OpenAI Python SDK |
| Lint and format | Ruff |
| Type checking | mypy (strict) |
| Testing | pytest, pytest-cov (unit + integration against real Postgres; LLM calls mocked at the HTTP layer) |
| Git hooks | pre-commit |

## Getting started

**Prerequisites:** Git, [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Docker Desktop. uv installs the correct Python version automatically.

```bash
git clone https://github.com/pranitdeshpande98/roost-hr-assistant.git
cd roost-hr-assistant
uv sync                          # Python 3.12 + all dependencies
uv run pre-commit install        # git hooks
cp .env.example .env             # then set POSTGRES_PASSWORD (and LLM keys, below)

docker compose up -d             # Postgres + pgvector
uv run alembic upgrade head      # create the schema
uv run python scripts/seed_db.py # 4,500 synthetic employees
uv run python scripts/ingest_handbook.py data/raw/handbook/handbook-v1.0.md \
    --version-label 1.0 --effective-date 2026-11-01 --publish
uv run python scripts/embed_handbook.py --model bge-m3   # first run downloads ~2 GB
```

**LLM keys (free, no credit card):** create a key at
[console.groq.com](https://console.groq.com) and at [openrouter.ai](https://openrouter.ai),
then add `GROQ_API_KEY` and `OPENROUTER_API_KEY` to `.env`. Check they work with:

```bash
uv run python scripts/llm_smoke.py --provider groq
```

The agent's model chain is set by `AGENT_MODELS` in `.env` (see `.env.example`); the
default is the chain chosen in [ADR-0009](docs/adr/0009-model-selection.md).

**Talk to Roost** in the terminal. `--employee` stands in for kiosk sign-in, which vacation
requests need; pick any employee number from the seeded data:

```bash
uv run python scripts/chat.py --show-tools                    # handbook questions
uv run python scripts/chat.py --language es --employee <number>  # vacation, in Spanish
```

## Development commands

| Task | Command |
|---|---|
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type check | `uv run mypy` |
| Run tests | `uv run pytest` |
| Unit tests only (no Docker) | `uv run pytest -m "not integration"` |
| Check models match migrations | `uv run alembic check` |
| Run all hooks | `uv run pre-commit run --all-files` |
| List a provider's live models | `uv run python scripts/llm_smoke.py --provider groq --list` |
| Smoke-test an LLM | `uv run python scripts/llm_smoke.py --provider groq --model <id>` |
| Embed with every candidate model | `uv run python scripts/embed_handbook.py --all` |
| Retrieval eval | `uv run python scripts/eval_retrieval.py` |
| LLM eval (resumable) | `uv run python scripts/eval_llm.py --provider groq --model <id>` |
| LLM eval report only | `uv run python scripts/eval_llm.py --report` |
| Chat with the agent | `uv run python scripts/chat.py --employee <number> --show-tools` |

## Project structure

```text
src/roost/        application package
  core/           settings, shared types, errors
  api/            HTTP layer
  agent/          orchestration and prompts
  tools/          tools the agent can call
  leave/          vacation rules (deterministic, no LLM)
  rag/            handbook parsing, ingestion and retrieval
  llm/            provider-agnostic LLM client
  guardrails/     sensitive topics, PII, prompt injection, disclosure
  auth/           kiosk login and roles
  db/             data models
  seed/           synthetic employee data
  adapters/       HR system, email, notifications (mocked)
  observability/  logging and tracing
  evals/          evaluation dataset loading and scoring
tests/            unit and integration tests
evals/            evaluation datasets and committed results
data/             handbook source files
migrations/       Alembic database migrations
scripts/          seed, ingestion, embedding, smoke-test, eval and chat commands
config/           reviewed reply templates (messages.yaml)
docs/             requirements, architecture and decision records
```

## Roadmap

- [x] Phase 1: Project foundation and tooling
- [x] Phase 2: Requirements and architecture
- [x] Phase 3: Data pipeline (handbook ingestion)
- [x] Phase 4: Model selection
- [x] Phase 5: Core logic (RAG and vacation agent)
- [ ] Phase 6: Evaluations
- [ ] Phase 7: Guardrails
- [ ] Phase 8: API layer
- [ ] Phase 9: Observability
- [ ] Phase 10: Containers and CI/CD
- [ ] Phase 11: Deployment
- [ ] Phase 12: Teams integration and post-launch
