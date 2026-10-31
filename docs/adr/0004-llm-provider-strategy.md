# ADR-0004: LLM provider strategy

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | NFR-01, NFR-02, NFR-05, NFR-08, NFR-09, NFR-10 |

## Context

Roost must run at $0 during development (no GPU, free tiers), but be ready for
production load by changing configuration, not code. Free tiers in October 2026
are tightly rate limited (e.g. Groq free: 30 requests/min, 1,000 requests/day,
200K tokens/day per model) and their model rosters change frequently. Some
free tiers use prompts to improve the provider's products, which is
unacceptable for real HR data. Groq, OpenRouter and Gemini all expose
OpenAI-compatible APIs.

## Options considered

### Provider approach

| Option | Pros | Cons |
|---|---|---|
| Single provider | Simplest | Rate limits or outages take the assistant down; violates NFR-10 |
| **Primary + fallback, routed by task** | Survives throttling and outages; per-model quotas stack; cheap tasks use small models | Must evaluate and monitor more than one model |
| Local model only (Ollama, CPU) | No data leaves the machine | Too slow on CPU for the 3 s first-token target; weak tool calling at small sizes |

### Client library

| Option | Pros | Cons |
|---|---|---|
| LiteLLM | Supports 100+ providers | Large dependency; PyPI releases were compromised in March 2026; we need only OpenAI-compatible endpoints |
| Provider-specific SDKs | Full feature access | One integration per provider; harder fallback |
| **OpenAI-compatible client behind our own interface** | One code path for all providers; small dependency surface; works with LangGraph tool calling and Pydantic structured output | Provider-specific extras are not used |

## Decision

1. All LLM calls go through `src/roost/llm/`, which exposes a small interface
   (chat with tools, structured output into a Pydantic model, streaming).
   No other module calls a provider directly.
2. The implementation uses the **OpenAI-compatible API format**, so providers
   are swapped by configuration: base URL, model name, API key.
3. **Two model roles**, each configured independently:
   - `agent` model: conversation and tool calling (larger model).
   - `fast` model: classification and extraction (smaller model).
4. **Fallback chain per role:** primary provider, then fallback provider, then a
   fixed response with HR contacts (NFR-10). Rate-limit and timeout errors
   trigger fallback; validation errors do not.
5. **Development defaults:** Groq free tier as primary; OpenRouter free models
   as fallback. Final models are selected by evaluation in Phase 4.
6. **Production rule:** only providers whose API terms exclude training on
   customer data may be configured for production. Gemini's free tier is
   excluded.
7. **Unit tests never call a real LLM.** They use a deterministic fake that
   implements the same interface.

## Candidate models for Phase 4 evaluation

Shortlisted from public leaderboards (Arena human-preference rankings) and
current free-tier availability. Public leaderboards do not measure tool calling
on our tools, grounding in our handbook, or Spanish and Vietnamese quality,
so the final choice is made by our own evals.

| Role | Candidate | Free access for evaluation | Notes |
|---|---|---|---|
| agent | gpt-oss-120b | Groq | Apache-2.0; strong reasoning |
| agent | Qwen 27B (current version on Groq) | Groq | Open weights; multilingual focus |
| agent | Gemma 4 31B | OpenRouter or Gemini API | Apache-2.0; highest Arena ranking of the three at release; free access paths are for evaluation only (see rule 6) |
| fast | gpt-oss-20b | Groq | Small and fast |
| fast | Gemma 4 small variant | OpenRouter or Gemini API | Evaluation only (see rule 6) |

If a model that is only freely available through a training-on-data tier wins,
production requires a paid host with a no-training policy.

## Consequences

- Moving from free to paid tiers, or to another provider, is a configuration
  change (NFR-09).
- Free-tier limits cannot meet NFR-02; load tests use a mock LLM, and the
  limitation is documented for the demo deployment.
- Model choices are revisited whenever free rosters change; evals (Phase 6)
  catch quality regressions when a model is swapped.
- API keys are read from environment variables only and never logged (NFR-04).
- Dependencies are pinned in `uv.lock` with hashes; LLM client upgrades are
  deliberate and reviewed.
