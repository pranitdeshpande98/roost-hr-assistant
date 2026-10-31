# ADR-0007: PostgreSQL with pgvector, SQLAlchemy 2.0 and Alembic

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | FR-24, FR-41, FR-44, FR-45, FR-48, NFR-03, NFR-04, NFR-08, NFR-09 |

## Context

Roost stores synthetic employees (mock HR system), vacation requests, a
permanent append-only audit log, conversation transcripts with 45-day
retention and legal hold, agent conversation state, kiosk PIN hashes, and the
handbook chunks with their vectors (ADR-0005). Development is on Windows;
production runs on free-tier hosting.

## Options considered

### Database

| Option | Pros | Cons |
|---|---|---|
| **PostgreSQL** | pgvector; transactions; JSON; full-text search; LangGraph Postgres checkpointer; free managed tiers | One more service to run locally |
| MySQL | Widely hosted | No pgvector; weaker fit for vectors and full-text search together |
| SQLite | Zero setup | Single writer; not for multiple app instances; no pgvector in managed form |

### Data access and migrations

| Option | Pros | Cons |
|---|---|---|
| Raw SQL | Full control | Hand-written mapping and no type safety |
| **SQLAlchemy 2.0 (typed, async) + psycopg 3** | Mature; typed models; async support | Learning curve |
| SQLModel | Pydantic-style models | Thin layer over SQLAlchemy; adds a dependency for little gain |
| **Alembic migrations** | Versioned, reviewable schema changes applied identically everywhere | Migration files must be maintained |

## Decision

1. **PostgreSQL** is the single application database, with the **pgvector**
   extension. Local and production use the same major version.
2. **SQLAlchemy 2.0** with typed models and async sessions, using the
   **psycopg 3** driver. Pydantic models (ADR-0003) remain the API and tool
   contracts; SQLAlchemy models are the storage layer, mapped in `src/roost/db/`.
3. **Alembic** manages every schema change as a versioned migration in Git.
   No manual schema edits in any environment.
4. **Local development** runs Postgres in Docker using the official pgvector image.
5. **Tests:** unit tests do not use a database. Integration tests run against
   real Postgres in Docker, never SQLite.
6. **Audit log is append-only at the database level:** the application's
   database role has INSERT-only rights on the audit table, and a trigger
   rejects UPDATE and DELETE.
7. **Retention:** a scheduled job deletes conversations older than 45 days
   unless a legal hold is set (FR-45, FR-48).
8. **Production host** (e.g. Neon or Supabase free tier) is chosen in the
   deployment phase; the application only needs a connection string.

## Consequences

- One database to operate, back up and secure for all application data.
- Schema history is reviewable in pull requests like code.
- The audit log cannot be altered by application bugs or a compromised
  application account.
- Developers need Docker Desktop (with WSL2 on Windows) for integration tests
  and local runs.
- Free-tier storage limits are far above our data size; growth beyond them is
  a hosting-plan change, not a design change.
