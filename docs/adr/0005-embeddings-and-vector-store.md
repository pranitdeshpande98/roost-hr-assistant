# ADR-0005: Local multilingual embeddings with pgvector and hybrid search

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | FR-01, FR-02, FR-03, FR-44, NFR-01, NFR-05, NFR-08 |

## Context

Roost retrieves handbook sections to ground every answer. Questions arrive in
English, Spanish and Vietnamese, while the binding handbook is English, so
retrieval must work across languages. The corpus is small (one handbook of ~20
sections, a few hundred chunks per version). Development is CPU-only at $0.
Publishing a new handbook version must atomically replace the old one (FR-44).
Exact terms (e.g. "FMLA", "AD&D", "401(k)") must be found reliably.

## Options considered

### Embedding model (all multilingual, CPU-sized, open weights)

| Option | Pros | Cons |
|---|---|---|
| EmbeddingGemma (~300M) | Top MTEB multilingual results under 500M parameters; small | Newer; must verify Vietnamese retrieval on our data |
| Qwen3-Embedding-0.6B | Strong MMTEB multilingual score; long context; adjustable dimensions | Larger memory footprint |
| multilingual-e5-large-instruct (560M) | Proven multilingual retrieval; MIT license | Older; 512-token input limit |
| bge-m3 (568M) | Dense + sparse vectors from one model; MIT; 8K context | Largest of the four |
| Hosted embedding API | No local compute | Rate limits, cost, and employee questions leave the service |

### Vector store

| Option | Pros | Cons |
|---|---|---|
| **pgvector (in Postgres)** | Same database as all other data; transactions for versioned publishing; SQL filtering; free on managed Postgres tiers | Not built for hundreds of millions of vectors (irrelevant at our size) |
| Qdrant | Fast; rich filtering; free cloud tier | A second database to run, secure and back up |
| Chroma / LanceDB (embedded) | Simple for prototypes | Separate store beside our main database; weaker operational story |

## Decision

1. **Vector store:** pgvector in the application's Postgres database (ADR-0007).
2. **Hybrid retrieval:** vector similarity and Postgres full-text search run
   together; results are merged with reciprocal rank fusion. Only chunks from
   the currently published handbook version are searchable.
3. **Embeddings run locally on CPU** inside the service; no external embedding API.
4. **Model selection:** candidates are EmbeddingGemma, Qwen3-Embedding-0.6B,
   multilingual-e5-large-instruct and bge-m3. The winner is chosen in Phase 4
   by retrieval evals (hit@5 ≥ 0.90) on questions in all three languages,
   weighing memory use on free-tier hosting.
5. Every stored vector records the **embedding model name and version**.
   Changing the model triggers a full re-embedding of the published handbook.

## Consequences

- One database to operate, back up and secure; handbook publish and retire
  happen in a single transaction.
- Query embedding adds a small, predictable CPU cost per question and no
  network call, supporting the 3 s first-token target (NFR-01).
- Free hosting tiers have limited memory, so model size is a selection
  criterion, not just quality.
- If the corpus grows far beyond one handbook (e.g. many policy documents per
  state), the store can move to a dedicated vector database behind the same
  retrieval interface in `src/roost/rag/`.
- A reranking step may be added later if evals show retrieval misses that
  hybrid search does not fix.
