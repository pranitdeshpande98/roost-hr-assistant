# ADR-0015: CI/CD and containers

- **Status:** Accepted
- **Date:** 2026-11-07
- **Phase:** 10 (containers and CI/CD)
- **Builds on:** ADR-0002 (modular monolith), ADR-0011 (agent evaluation), ADR-0012
  (guardrails), ADR-0014 (observability)

## Context

Until Phase 10, every check ran only on the developer's machine: the pre-commit hooks
on commit, and the tests when someone remembered. NFR-12 asks for code that is "type-checked, linted, ≥ 80%
unit-test coverage on core packages; CI blocks merges on failure", and requirements 6.2
for quality gates "enforced by evals in CI". Roost also had no container: Phase 11
needs one to deploy.

Three facts shaped the design:

| Fact | Consequence |
|---|---|
| The LLM evals call free-tier models: one full agent run is about 178k tokens, close to gpt-oss-120b's 200k daily limit | They cannot run on every pull request |
| torch from PyPI on Linux depends on several GB of NVIDIA GPU libraries Roost never uses (embeddings run on CPU) | CI installs and the image would be slow and huge |
| The embedding model (bge-m3) is about 2.2 GB | Baking it into the image would triple its size |

Decisions made with the project owner: the evals run **weekly and on demand**, and the
image **downloads the model on first start**.

## Decision

### 1. CI on every push to master and every pull request

`.github/workflows/ci.yml`, two jobs, about 5–8 minutes:

| Job | Steps |
|---|---|
| **Lint, types and tests** | `uv sync --frozen`; Ruff lint (annotated on the PR) and format check; mypy strict; the file-hygiene pre-commit hooks; `alembic upgrade head` then `alembic check` (the migrations match the models); all tests against **pgvector/pgvector:pg17**, the image in `compose.yaml`; coverage gate |
| **Docker image builds** | Builds the image with the layer cache, then checks that it runs as a non-root user, that the app imports, and that torch is the CPU build |

- **A database CI cannot reach is a failure**, never a skip (`REQUIRE_INTEGRATION=1`):
  locally the integration tests skip without Docker, but in CI a silent skip would hide
  their absence.
- **Coverage must stay at or above 90%** (`fail_under` in `pyproject.toml`). NFR-12 asks for
  80% on core packages; Roost is at 96% overall and above 90% in every package, so the
  gate is set where the code already is.
- **Least privilege:** the workflow token can only read the repository, and checkout keeps
  no credentials. A newer push cancels the run it replaces.

### 2. Merges blocked on failure (NFR-12)

Branch protection on `master` requires both CI jobs to pass, with the branch up to date,
and applies to administrators too. Without it CI would only advise. It is set once with
`gh api` (README).

### 3. The model evals: weekly and on demand

`.github/workflows/evals.yml`, Mondays 07:23 UTC and from the Actions tab (all suites,
or one):

1. Builds the database exactly as the README does: migrate, seed, ingest the handbook,
   embed with bge-m3 (the model is cached between runs).
2. Runs the three suites, each allowed to fail without stopping the others:

| Suite | Gate (requirements 6.2) |
|---|---|
| Retrieval | hit@5 at least 90% for `vector:bge-m3`, the search the agent uses (new `--gate` option) |
| Guardrails | the frozen holdout: recall, precision, injection blocking (ADR-0012) |
| Agent | 34 scenarios, 102 cases, all six gates (ADR-0011) |

3. Attaches every report to the run, shows them on its summary page, and fails if any
   gate failed.

- **Keys come from repository secrets** (`GROQ_API_KEY`, `OPENROUTER_API_KEY`). The run
  stops with a clear message if they are missing.
- **Results are not committed.** Committed reports stay the reviewed baselines; the weekly
  run checks the current code still meets the gates.
- **Pull requests never spend model quota.** The deterministic gates (validation rules
  V-01 to V-09, citation cleaning, the guardrail's keyword layer, routing and templates)
  are unit tests, so they do run on every PR.

### 4. The container

```text
build:  python:3.12-slim + uv 0.11.32 ─► uv sync --frozen --no-dev (dependencies first, then the code)
run:    python:3.12-slim ─► /app (venv + code), user roost (uid 10001), port 8000,
        health check on /health, HF_HOME=/models (a volume: bge-m3 downloaded once)
```

- **Two stages:** the run stage has no build tools, and is about 1.5 GB, mostly torch
  and the transformer libraries.
- **CPU-only torch on Linux:** torch is listed directly in `pyproject.toml` (uv applies an
  index only to direct dependencies) with PyTorch's CPU index for Linux. Windows and macOS
  keep PyPI, whose wheels are CPU-only there anyway, so nothing changes on the
  developer's machine. The lock went from torch plus 16 NVIDIA packages to `torch 2.15.0+cpu`.
- **Nothing private in the image:** `.dockerignore` leaves out `.env`, `.git`, the tests,
  local caches and eval results. Settings and keys come from the environment at run time.
- **`compose.yaml` gains an `app` service under a profile.** `docker compose up -d` still
  starts only Postgres and Jaeger for day-to-day work with uv, while
  `docker compose --profile app up -d` runs the whole stack. The app reaches the database
  and Jaeger by service name, applies migrations, then serves on `127.0.0.1:8000`.
- **Not published yet:** building and checking is CI's job; pushing to a registry comes
  with Phase 11, once the hosting is chosen.

### 5. Dependency updates

Dependabot opens weekly pull requests for Python packages (minor and patch grouped),
GitHub Actions and the Docker base images. Each one goes through CI and branch
protection like any other change.

## Why not the alternatives

| Alternative | Why not |
|---|---|
| Model evals on every pull request | Each PR would spend most of a day's quota and fail whenever the free tier is busy; that trains people to ignore red builds. |
| Model evals only when someone remembers | Regressions from a model provider's silent update would go unnoticed for weeks. |
| SQLite or a mocked database in CI | The app relies on Postgres features (pgvector, JSONB, partial indexes, append-only triggers); only the real engine tests them. |
| The embedding model baked into the image | About 4 GB, slower to build and push, for a one-time download. |
| PyPI torch on Linux | Several GB of GPU libraries in every CI install and image layer, for nothing. |
| Pinning actions by commit hash | Stronger supply-chain protection, but unreadable for a portfolio project; Dependabot keeps the version tags current, and setup-uv v8's tags are immutable. |
| Committing weekly eval results automatically | The workflow would need write access, and the reviewed baselines would churn with free-tier noise. |

## How it was tested

- **The CI steps were run locally in workflow order**, against a fresh Postgres database
  with `REQUIRE_INTEGRATION=1`: Ruff, the format check, mypy, the hygiene hooks, the
  migration check, and 870 tests at 96.23% coverage (gate 90%).
- **Both workflows pass actionlint**, the standard checker for GitHub Actions syntax,
  expressions and shell steps.
- **The image's install steps were rehearsed without Docker:**
  - a copy of the project filtered by `.dockerignore` contained no `.env` and no tests;
  - `uv sync --frozen --no-dev` installed `torch 2.15.0+cpu` and no pytest;
  - the app imported and found its configuration files;
  - the environment came to 1.3 GB.
- **The retrieval gate** was checked on known rows: 98% passes, 89% fails, and an
  unknown method fails.
- **What only GitHub can show** (the first CI run, the image build with Docker, and the
  first eval run with the secrets) is confirmed on the Phase 10 pull request.

## Consequences

### Positive

- No change reaches `master` without lint, types, tests against real Postgres, a migration
  check and a working image.
- Model regressions surface within a week, with the reports attached.
- The image is ready for Phase 11, and anyone can run the whole stack with one command.

### Negative and risks

- **A quality regression can sit on `master` for up to a week** before the weekly eval
  catches it. Changes to prompts, models or guardrail rules should run the evals by hand
  before merging (Actions → Evals → Run workflow).
- **The weekly agent eval uses most of that day's free-tier quota.** Monday at 07:23 UTC
  was chosen to keep it away from working hours.
- **Free-tier limits can fail an eval run** that is otherwise fine. The reports show
  whether a gate failed or the provider refused calls.
- **Branch protection applies to the owner too:** an emergency fix still needs a green PR.
- **The first container start downloads 2.2 GB**, and the health check allows 3 minutes for it.

### When to revisit

- Phase 11: publish the image (GitHub Container Registry is free for public repositories),
  scan it, and deploy from a tagged build; move retention, reminders and outbox delivery
  to scheduled jobs.
- If the free tier allows it, run the agent eval on pull requests that touch prompts,
  models or guardrail rules.
