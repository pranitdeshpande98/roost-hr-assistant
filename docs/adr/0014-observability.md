# ADR-0014: Observability: tracing, metrics, transcripts and streaming

- **Status:** Accepted
- **Date:** 2026-11-07
- **Phase:** 9 (observability)
- **Builds on:** ADR-0010 (agent design), ADR-0012 (guardrails), ADR-0013 (API layer)

## Context

By the end of Phase 8, Roost worked end to end but was opaque. Nobody could see how long
a turn took, which model answered, what it cost, or how often the handbook had no
answer. There was no record of what was said, so the 45-day transcript policy and legal
holds had nothing to act on. Phase 9 had to cover:

| Need | Requirements |
|---|---|
| A trace per turn: retrieval, tools, guardrail, latency, tokens, cost | NFR-11 |
| Tokens and an estimated cost per conversation | NFR-08 |
| The first part of the answer within 3 s at p95 | NFR-01 |
| No personal data in logs and traces | NFR-05 |
| Sensitive topics counted only as categories | FR-49 |
| Transcripts kept 45 days, unless HR places a legal hold, audited | FR-45, FR-48 |
| HR admins read transcripts (P1) | FR-46 |
| Employees rate answers (P1) | FR-47 |
| Quality signals: answers and "I don't know" by language, ratings, fallbacks (P1) | FR-51 |

Carried over from earlier phases: a vacation confirmation question vanished from the
kiosk transcript once answered (ADR-0013), and an alert was needed for when the
guardrail falls back to similarity (ADR-0012).

Decisions made with the project owner before building:

1. **Tracing:** OpenTelemetry, sent to local Jaeger (both free and open source).
2. **Scope:** all three P1 items (FR-46, FR-47, FR-51).
3. **Streaming:** stream the model's text as it comes, then swap in the checked text at
   the end, accepting that an unsupported citation may show for a moment before it is
   removed.

## Decision

### 1. One trace per request, one span per step

```text
POST /chat/messages · GET /chat/stream (FastAPI)
└── roost.turn            channel, language, reply kind, category, tokens, cost
    ├── agent.screen      guardrail category and source
    │   └── llm.chat      classifier model (purpose "guardrail")
    ├── agent.call_model
    │   └── llm.chat      model, tokens, cost, finish reason, fallback position
    ├── agent.run_tools
    │   └── tool.call     tool name, outcome; for the handbook: hits and section numbers
    └── agent.finalize
```

- **OpenTelemetry SDK, exported over OTLP/HTTP.** Locally to Jaeger 2.21 (`compose.yaml`,
  UI on `localhost:16686`, reachable only from the machine itself). Any OTLP backend later
  is a setting: `OTEL_EXPORTER_OTLP_ENDPOINT`. Left empty, tracing is off and costs nothing.
- **FastAPI is auto-instrumented**, leaving out static files and the health check. The
  SQLAlchemy add-on was dropped: it does not support SQLAlchemy 2.1, and the request and
  turn spans already show where time goes.
- **Logs are JSON lines** carrying the trace id, so a log line leads to its trace.

### 2. What a trace may contain

Every attribute Roost sets goes through one filter (`observability/attributes.py`):

- **Keys must be on a fixed list** (`roost.*`, and the OpenTelemetry `gen_ai.*`
  conventions where they exist).
- **Text values must be a short token without spaces:** a model name, a category, a
  section number. A sentence can never pass, even by mistake.
- **Exceptions are recorded by type only:** no message, no stack trace, because a message
  can quote input.

A test sends a harassment message through a whole turn and checks that none of its
words reach any span or log line.

### 3. A row of numbers per turn

`turn_metric` holds, for each turn:

- the channel, language and reply kind (answer, routed, question, fallback, error);
- the guardrail category and source;
- the tools used, and their errors and refusals;
- handbook searches, and whether an answer cited nothing ("unanswered");
- the answering model, whether a fallback answered, and model failures;
- tokens, estimated cost, latency and time to first token.

It never holds text or who the employee was, and is kept 30 days. **Estimated cost** uses
public list prices in `config/model_prices.yaml` (Groq, checked 2026-11-07). The free tier
bills $0; the figure shows what the same traffic would cost on a paid plan. A test checks
every configured model has a price.

### 4. Transcripts are a record of their own

```text
kiosk turn ─► message (employee) + message (Roost, exactly as shown) ─► conversation
```

- **The kiosk draws its screen from this record.** What the employee saw and what HR can
  review are the same text. This fixes the vanishing confirmation question: the question
  is saved like any reply, and the YES after it as an answer.
- **A `category` column on routed turns** lets them be counted without being read (FR-49).
- **HR admins only** (FR-46) can list and open transcripts, never their own. **Every view
  is audited** (`transcript_viewed`: who and which, never what).
- **Legal hold** (FR-48): HR admins place it with a reason, such as a case number, or
  release it; both are audited. A held conversation is never deleted.

### 5. One retention run

`scripts/apply_retention.py`, daily:

| Store | Kept | Then |
|---|---|---|
| Transcripts, and their agent memory (LangGraph checkpoints) | 45 days after the last message | deleted, unless on legal hold |
| Agent memory with no transcript | while its kiosk session is open | deleted |
| Turn metrics | 30 days | deleted |
| HR referral texts (ADR-0013) | 90 days after HR handled them | the words erased, the record kept |

The audit log is never touched (FR-24).

### 6. Streaming at the kiosk

```text
POST /chat/messages (X-Roost-Stream: 1) ─► held on the session; the page shows the message
                                           and an empty Roost bubble at once
GET  /chat/stream (server-sent events)   ─► token · token · … · [reset] · done (final HTML)
```

- **Only the agent's own answer streams.** The guardrail classifier never does. Each model
  step first resets the stream, so text a model writes before deciding to call a tool is
  dropped.
- **A fallback mid-answer sends `reset`:** the bubble empties and the next model refills it.
- **`done` carries the finished conversation:** the checked text (unsupported citations
  removed, markdown cleaned, footer added), exactly as recorded. It replaces the bubble.
- **A message runs once.** The POST stores it; the stream takes it and clears it. A
  reconnect, or a stream with no session, gets 204, which tells the browser to stop
  reconnecting. The security test checks this explicitly.
- **Without JavaScript**, or without the header, the POST runs the turn and returns the
  reply in one go, as before.
- **Time to first token** is recorded per turn. That is what NFR-01 is measured against.

### 7. Ratings and the Quality page

- **Ratings (FR-47):** "Was this helpful? 👍 / 👎" under each real answer (not under
  questions or fixed replies). An employee can rate only answers in their own current
  conversation. Stored on the transcript message.
- **Quality page (FR-51)** in the console, for HR admins, over 24 hours, 7 days or 30 days:
  - messages, typical and p95 reply time, and p95 time to first word against the 3 s
    target;
  - per language: answers, "no handbook answer found", replies sent to HR or 911, and
    the helpful rate;
  - guardrail decisions by topic and by what decided them;
  - per-model turns, tokens and estimated cost.
- **Warnings** on the page:
  - the similarity backup check decided anything in the last 24 hours (the AI classifier
    was down);
  - the p95 time to first word missed the target;
  - over 1 in 5 replies came from a fallback model.

  The similarity case is also logged as a WARNING, with the category and never the words,
  for Phase 11 to alert on.

## Why not the alternatives

| Alternative | Why not |
|---|---|
| Traces only in Postgres, no OpenTelemetry | A home-made format no tool can read; OpenTelemetry costs nothing extra and lets Phase 11 pick any backend. |
| A hosted tracing service now | Needs an account, and sends data off the machine before the Phase 11 decisions on where Roost runs. |
| Recording message text in traces "for debugging" | Traces outlive their purpose and are read by engineers; the transcript, with its access rules and audit, is the only place for words. |
| LangGraph checkpoints as the transcript | They hold the model's working history (tool calls, internal notes), not what the employee saw, and the confirmation question was not in them. |
| Holding back citations until checked, while streaming | Chosen against by the owner for simplicity; the checked text replaces the bubble within the same turn. |
| WebSockets for streaming | Two-way connections are not needed; server-sent events are plain HTTP and work through every proxy. |

## How it was tested

870 tests in total. The ones for this phase:

| Area | What the tests prove |
|---|---|
| Spans (`test_turn_tracing.py`, `test_client.py`) | One trace per turn with a span per step; model spans carry tokens, cost and finish reason; failures record only the error type; pausing for YES/NO is not an error; a sensitive message leaves no word in spans or logs |
| Attribute filter (`test_attributes.py`) | Listed keys with token values pass; sentences, unknown keys and mixed lists are dropped |
| Cost (`test_prices.py`, `test_turn_metrics.py`) | List-price arithmetic; every configured model priced; a kiosk turn saves one row with tokens and cost and no text, inside the request's trace |
| Streaming (`test_client.py`, `test_fallback.py`, `test_kiosk_streaming.py`) | Text streamed as it arrives and reassembled, tool calls included; a fallback resets the stream; the POST answers at once; the stream ends with the checked text (an unreturned citation removed); a message runs once; confirmations come through the stream |
| Transcripts (`test_transcripts.py`) | The question stays on screen and in the record; only routed turns carry a category; only HR admins read, never their own, each view audited; holds need CSRF and are audited; retention deletes after 45 days except held, and forgets unused agent memory in the real Postgres checkpointer |
| Quality (`test_quality.py`) | Ratings only on one's own answers; counts, percentiles and cost per language and model; the similarity warning within 24 hours; only HR admins see the page |
| Security (`test_http_security.py`) | The route-list tests caught every new form (holds, ratings) and the new stream route as they were added; each was then covered, and the CSRF check verified by removing it and watching the test fail |

Tried by hand on Windows: traces in Jaeger after `docker compose up -d jaeger`, a kiosk
answer through the stream ending with its citation and footer, and the transcript pages.

## Consequences

### Positive

- Any slow or odd turn can be followed from the HTTP request to the model call that
  caused it, without anyone's words leaving the transcript.
- HR has the record the policy needs, with holds and an audit of who read what.
- The kiosk feels faster: the first words arrive while the model is still writing.
- Quality is visible by language, which matters most for the Spanish and Vietnamese
  answers the evals found weakest.

### Negative and risks

- **An unsupported citation can flash** during streaming before the checked text
  replaces it: the chosen trade-off. Phase 7's citation guardrail still decides what is
  recorded and stays on screen.
- **Jaeger keeps traces in memory**: they are gone when its container restarts. Fine for
  local use; Phase 11 picks a backend with storage.
- **Retention is a script nobody schedules yet** (Phase 11), like the reminders and outbox.
- **The pending message sits on the session row** for the second or two between the POST
  and the stream. It is cleared when taken; a stream that never comes leaves it until the
  session ends (at most 2 idle minutes).
- **"Unanswered" is approximate:** an answer that searched the handbook and cited nothing.
  A correct "that is not in the handbook" counts too, so the rate is an upper bound.
- **The client address is in the HTTP spans** (OpenTelemetry's standard
  `client.address`). For a kiosk that identifies a machine, not a person; for the console
  it is an approver's computer. Kept 30 days with the rest of the telemetry.

### When to revisit

- Phase 10: run the agent eval in CI and post the Quality numbers with each change.
- Phase 11: schedule retention, reminders and outbox delivery; choose a trace backend
  with storage; alert on the similarity-fallback warning and on missed latency targets.
- If the citation flash is reported as confusing, hold back citations while streaming
  (the alternative above), at the cost of a short pause at each citation.
