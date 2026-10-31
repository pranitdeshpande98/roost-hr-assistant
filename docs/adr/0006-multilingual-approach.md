# ADR-0006: Cross-lingual retrieval with direct answers in the user's language

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | FR-01, FR-03, FR-04, FR-30, FR-31, NFR-01, NFR-07, NFR-08 |

## Context

Employees ask questions in English, Spanish or Vietnamese. The English handbook
is the only binding source (handbook Section 1.4). Answers must be in the
user's language, cite English handbook sections, and state that the English
version is binding. Free-tier LLM quotas are small, so extra LLM calls per
question are costly. Safety-critical messages (emergencies, sensitive-topic
routing) must be correct in every language.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| Translate question to English, answer in English, translate back | Core pipeline is English-only | Three LLM calls per question; latency; translation errors compound |
| **Cross-lingual retrieval, answer directly in the user's language** | One main LLM call; multilingual embeddings match questions to English chunks | Depends on the model's Spanish/Vietnamese quality; keyword search cannot match non-English questions on its own |
| Maintain translated handbooks and index them | Native-language retrieval and citations | Translations need legal review and syncing with every version; planned as P1 |

## Decision

1. **Retrieval:** the original question is embedded with the multilingual
   model (ADR-0005) for vector search. The `fast` model also produces a short
   English search query, used for keyword search.
2. **Generation:** the `agent` model receives the English handbook chunks and
   answers in the user's language, citing section numbers and titles from the
   English handbook. Non-English answers include the fixed notice that the
   English handbook is binding.
3. **Language selection:** kiosk users choose a language on the start screen.
   In Teams, a lightweight language-detection library (not the LLM) detects
   the language of each message; very short or ambiguous messages keep the
   conversation's current language. Users can switch language at any time.
4. **Fixed templates for safety-critical text:** the disclaimer, emergency
   instructions, sensitive-topic routing messages, HR contact details and the
   ethics hotline message are pre-written, human-reviewed templates in all
   three languages. The LLM never generates or translates them.
5. **Glossary:** key HR terms have fixed translations in a configuration file
   (`config/glossary.yaml`) that is included in the agent prompt.
6. **Evaluation:** every eval set (Phase 6) includes English, Spanish and
   Vietnamese cases, and metrics are reported per language.

## Consequences

- One `agent` call plus one `fast` call per question keeps latency and quota
  use low.
- Answer quality in Spanish and Vietnamese is measured, not assumed; a model
  that is weak in Vietnamese fails the per-language evals and is not selected.
- Safety-critical messages are identical and correct in every language because
  they bypass the LLM.
- Guardrail classifiers must be evaluated in all three languages.
- Adding a fourth language requires new templates, glossary entries and eval
  cases, but no code changes.
- Translated handbooks (P1) can later be indexed alongside the English one
  without changing this design.
