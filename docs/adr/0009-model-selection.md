# ADR-0009: Embedding model, retrieval method and agent LLM chain

- **Status:** Accepted
- **Date:** 2026-11-01
- **Phase:** 4 (model selection)
- **Amends:** ADR-0005 (hybrid search is not adopted; see decision 2)

## Context

Roost answers HR policy questions from a 133-section English handbook for employees who
write in English, Spanish or Vietnamese. The constraints from the requirements are:

- **Grounded answers only:** every answer must come from a handbook section and cite it.
- **Three languages,** with the handbook in English only (cross-lingual retrieval, ADR-0006).
- **Free tier and CPU only:** embeddings run locally on a laptop CPU; LLMs use free API tiers.
- **Tool calling:** the agent must call `search_handbook` rather than answer from memory.
- **Kiosk latency:** an hourly employee is standing at a screen; answers must feel instant.

ADR-0004 chose OpenAI-compatible providers with a primary and fallback per role, and
ADR-0005 planned hybrid (keyword + vector) search. This ADR records what the evals showed
and what we chose.

## Evaluation setup

Both evals use `evals/datasets/retrieval_v1.yaml`: 30 employee questions, each written in
English, Spanish and Vietnamese (90 cases), with the handbook sections that answer them.
Questions use everyday wording on purpose, not the handbook's own phrasing.

| Eval | What varies | Fixed | Metric | Reproduce |
|---|---|---|---|---|
| Retrieval | embedding model x search method | handbook v1.0 | hit@5, MRR | `uv run python scripts/eval_retrieval.py` |
| LLM | chat model | bge-m3 vector search, top 3 | 4 pass/fail checks, latency, tokens | `uv run python scripts/eval_llm.py --report` |

LLM checks, all deterministic (no LLM-as-judge):

- **Searched:** the model's first action is a `search_handbook` call.
- **Right language:** the answer is in the question's language.
- **Valid cites:** at least one `[§x.y]` citation, and only sections the search returned.
- **Correct section:** at least one citation is an expected answer section.

Raw results are committed in `evals/results/` (per-case JSON/JSONL plus summaries).

## Results

### Retrieval (90 cases, top 5)

| Method | EN hit@5 | ES hit@5 | VI hit@5 | All hit@5 | MRR |
|---|---|---|---|---|---|
| Keyword (Postgres FTS) | 73% | 10% | 7% | 30% | 0.246 |
| **Vector: bge-m3** | **100%** | **100%** | **93%** | **98%** | **0.813** |
| Hybrid (RRF): bge-m3 | 87% | 93% | 90% | 90% | 0.723 |
| Vector: qwen3-embedding-0.6b | 93% | 97% | 93% | 94% | 0.800 |
| Hybrid (RRF): qwen3-embedding-0.6b | 83% | 90% | 90% | 88% | 0.734 |
| Vector: multilingual-e5-large-instruct | 97% | 97% | 87% | 93% | 0.788 |
| Hybrid (RRF): multilingual-e5-large-instruct | 87% | 93% | 87% | 89% | 0.735 |

EmbeddingGemma-300m was not evaluated: it is gated behind a Hugging Face licence and
account, which conflicts with a reproducible, credential-free local setup.

### LLM (agent role)

| Model | Cases | Errors | Searched | Right language | Valid cites | Correct section | Median s | p90 s | Tokens/case |
|---|---|---|---|---|---|---|---|---|---|
| groq/openai/gpt-oss-120b | 90 | 0% | 100% | 100% | 100% | 94% | 1.0 | 1.3 | 1042 |
| groq/openai/gpt-oss-20b | 90 | 0% | 100% | 100% | 98% | 91% | 0.8 | 1.2 | 971 |
| groq/qwen/qwen3.8-27b | 90 | 0% | 100% | 100% | 100% | 97% | 0.8 | 1.4 | 997 |
| openrouter/nvidia/nemotron-3-ultra-550b-a55b:free | 15 | 27% | 87% | 73% | 73% | 73% | 14.9 | 27.0 | 1179 |

OpenRouter was sampled on 15 cases (5 intents x 3 languages) because its free tier allows
50 requests per day per account. `google/gemma-4-31b-it:free` was also tried and was
rate-limited by its upstream provider on every attempt.

## Decision

### 1. Embedding model: `BAAI/bge-m3`

Best overall hit@5 (98%) and MRR (0.813), and the only model at 100% for both English and
Spanish. It runs on CPU in float32, needs no instruction prompt, and is not gated.

### 2. Retrieval: vector search only, top 3 (amends ADR-0005)

Hybrid search with reciprocal rank fusion was **worse than vector search alone for every
model** (bge-m3: 90% vs 98%). Two causes:

- Keyword search uses English stemming, so for Spanish and Vietnamese questions it returns
  mostly noise (10% and 7% hit@5).
- RRF rewards sections that appear in both lists, even near the bottom, so the noisy
  keyword list displaced correct vector results at the top. Tuned variants (vector weight
  x3, keyword search only for English) peaked at 94%, still below vector-only.

The keyword and hybrid functions stay in `roost.rag.retrieval` (tested, unused by the
agent) so the comparison can be re-run when the handbook changes.

### 3. Agent LLM chain

Configured by `AGENT_MODELS` (comma-separated `provider:model`, primary first):

| Order | Model | Why |
|---|---|---|
| Primary | `groq:openai/gpt-oss-120b` | 100% valid cites, 94% correct, ~1 s; Groq **production** tier |
| Fallback 1 | `groq:qwen/qwen3.8-27b` | Best correct-section score (97%); different model family; separate per-model quota |
| Fallback 2 | `openrouter:nvidia/nemotron-3-ultra-550b-a55b:free` | Keeps Roost answering if Groq is down; accurate when it answers, but slow and unreliable |

**Why gpt-oss-120b is primary even though Qwen scored higher:** 97% vs 94% is 87 vs 85
correct answers out of 90, too small a gap to be meaningful at this sample size. Model
lifecycle is a real difference: Groq listed only the GPT-OSS models as production, and
removed preview models (Qwen3 32B, Llama 4 Scout) with little notice in July 2026. The
primary path should not depend on a preview model. If Groq promotes Qwen3.8 27B to
production, swapping the first two entries is a configuration change.

**Rejected:** `gpt-oss-20b`, the only Groq model that cited a section it was never shown
(98% valid cites). Uncited or invented sources are the failure this project most needs to
avoid, and the 0.2 s it saves does not justify the risk.

## Consequences

### Positive

- Decisions rest on committed, reproducible evidence rather than leaderboard scores.
- Swapping or reordering models is a `.env` change; no code release is needed.
- The fallback chain only moves on for outages (timeouts, 429, 5xx, empty replies); bad
  requests and bad keys fail loudly instead of being hidden by a fallback.
- Groq rate limits are per model, so the first two links fail independently.

### Negative and risks

- **Provider catalogs change often.** Groq removed two models in 2026; OpenRouter free
  models rotate without notice. Mitigation: `scripts/llm_smoke.py --list` before changes,
  and the eval is resumable and cheap to re-run.
- **Fallback 2 is slow** (median 14.9 s, p90 27 s) and failed 4 of 15 requests. It is a
  last resort for full Groq outages, not a normal path; the kiosk UI must show a "still
  working" state for it.
- **Free tiers cap daily volume** (OpenRouter: 50 free requests per day without credits).
  Fine for a portfolio deployment; a real rollout would need paid capacity.
- **Tool queries are rewritten into English by the LLM.** Retrieval quality on Spanish and
  Vietnamese therefore depends partly on the LLM's translation, not only on bge-m3.

### Limitations of the evidence

- The question set was written for this project and has not yet been reviewed by native
  Spanish or Vietnamese speakers.
- The checks verify search, language and citations, not the full correctness of each
  answer's wording; human review of a sample is planned for the Phase 6 evals.
- Latency was measured from one laptop in one session; production latency will differ.

### When to revisit

- A model in `AGENT_MODELS` is deprecated, or Groq's catalog changes tier status.
- A new handbook version is published, or the handbook grows well past 133 sections.
- The question set is reviewed by native speakers or expanded.
- Users ask many exact-term questions (form names, codes), where keyword search may help.
