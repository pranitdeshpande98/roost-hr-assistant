# ADR-0013: API layer: kiosk and approver console, approvals, notifications and referrals

- **Status:** Accepted
- **Date:** 2026-11-07
- **Phase:** 8 (API layer)
- **Builds on:** ADR-0007 (relational database), ADR-0008 (channel adapters), ADR-0010
  (agent design), ADR-0012 (guardrails)

## Context

Through Phase 7, Roost was a library and a terminal chat. Phase 8 had to make it
something employees and approvers can use, and finish the parts of the vacation workflow
that need people other than the employee:

| Need | Requirements |
|---|---|
| Kiosk sign-in with a PIN, safe on a shared screen | FR-40, FR-41, NFR-06 |
| Supervisor and HR approval, in the order the handbook sets | FR-20, FR-21, FR-13, FR-14 |
| Decisions and reminders reach people on Teams, email or the kiosk | FR-22, FR-23 |
| A permanent record of every action | FR-24 |
| With consent, a short summary of a sensitive matter to HR | FR-32 (moved here by ADR-0012) |
| People see only what is theirs or routed to them | FR-43, NFR-04 |

Decisions made with the project owner before building:

1. **Sessions live in Postgres**, not in signed browser tokens.
2. **The kiosk shows "working…" and then the whole reply.** Token streaming waits for
   Phase 9, which adds the telemetry needed to watch it.
3. **The HR summary is the employee's own words**, never a model's rewording.
4. **Hours.** Everyone may see the hours a request uses. HR (not supervisors) may see
   the hours the employee would have left. This is the one exception to FR-06 (section 9).

## Decision

### 1. One FastAPI app, two server-rendered web apps

```text
FastAPI ─┬─ kiosk    /sign-in · /change-pin · /chat · /chat/messages · /sign-out
         ├─ console  /console/sign-in · /change-password · /queue · /approvals/{id}
         │           /history · /referrals · /referrals/{id} · /sign-out
         └─ /health (database check, for the load balancer)
```

- **Jinja2 pages that work without JavaScript.** htmx 2.0.11 (vendored, 0BSD) only
  makes sending a chat message smoother; the kiosk's idle timer is a few lines of plain JS.
- **Every response** carries `Cache-Control: no-store`, `X-Frame-Options: DENY`, a
  Content Security Policy that allows scripts and styles only from this server (none
  inline), and no referrer. htmx keeps no page history (`historyCacheSize: 0`).
- **No interactive API docs** (`/docs`, `/openapi.json`): nothing here is for the public.

### 2. Server-side sessions, one table for both apps

`web_session` holds the SHA-256 of a random token; the browser holds only the token, in
an `HttpOnly; Secure; SameSite=Strict` cookie with no expiry date.

| | Kiosk | Console |
|---|---|---|
| Cookie | `roost_session`, path `/` | `roost_console`, path `/console` |
| Idle limit, checked by the server on every request | 2 minutes (FR-40) | 15 minutes |
| Ends when | sign-out, idle, or the employee is no longer active | sign-out, idle, inactive, or the console role is taken away |

- **A session belongs to one app** (`app` column): a kiosk token opens nothing in the
  console, and the other way round.
- **Every form carries the session's CSRF token.** Sign-in and sign-out are the only
  exceptions (there is no session yet, and the idle sign-out must always work).
- **A new PIN or password replaces the token** and the CSRF token, so a cookie copied
  before the change stops working.
- **Sign-out** deletes the row and sends `Clear-Site-Data`. Idle rows are swept at every
  sign-in.

### 3. Kiosk sign-in: employee number and a 6-digit PIN

- **The PIN policy** refuses repeats, sequences, a short list of common PINs and seven
  ways of writing the employee's birth date (FR-40).
- **Argon2id** with the library defaults (about 50 ms and 64 MiB per hash); hashes
  are upgraded at sign-in when the settings change.
- **Five failures lock the account for 15 minutes** (audited). Each kiosk is also limited
  to 10 attempts a minute, so one kiosk cannot try one PIN against many numbers.
- **Every failure looks the same**: an unknown number, an inactive employee and a wrong
  PIN get the same message after the same Argon2 check.
- **HR sets a temporary PIN in person** (`scripts/set_pin.py`), only for employees at
  their own location (HR admins: anyone). The employee must choose their own before
  anything else works (FR-41).

### 4. Console sign-in: local passwords now, Entra ID later

- **Only supervisors and HR can use it**, checked at sign-in and on every page.
- **Passwords**: at least 12 characters, not a well-known password, not containing the
  person's name or employee number. Same Argon2id hashing and lockout as PINs.
- **HR hands out temporary passwords** (`scripts/set_console_password.py`, four groups of
  four characters, about 79 bits), never for themselves.
- **Sign-in goes through a `ConsoleAuthenticator` interface**, so Phase 12 can replace
  local passwords with Microsoft Entra ID (FR-42) without touching the pages.

### 5. Approvals in rounds

```text
submit or change ─► round N: supervisor step ─approve─► HR step (employee's location) ─approve─► approved
                                  └──deny──► denied            └──deny──► denied
CEO (no supervisor) ─► approved at once, Corporate HR told
```

- **Each submit or change starts a new round** (FR-13). A decision on an older round is
  refused, so nobody approves dates that are no longer the request.
- **Who may decide**: the supervisor step only its assignee; the HR step only an HR
  generalist at the employee's location or an HR admin, and only after the supervisor
  approved. Nobody decides their own request. The approval row and the request are locked
  while deciding, so two approvers cannot both win.
- **Approved hours are posted** to `leave_balance.used_hours`, on the same row the
  vacation check reads, and given back when an approved request is changed or cancelled.
  Before this, only pending requests counted against the balance.
- **Cancelling** (FR-14) tells the supervisor, and HR once it had reached them.

### 6. Notifications through an outbox

Code that decides something writes a `notification` row in the same transaction, so a
decision and its notice are saved together or not at all. Delivery is separate.

| Channel (FR-22) | Who | Delivery now | Later |
|---|---|---|---|
| Teams | has a Teams account | `scripts/deliver_outbox.py` writes JSON files to `var/outbox/` | Phase 12 |
| Email | has an email, no Teams | same | a mail service |
| Kiosk | neither (drivers) | shown once on the chat page after sign-in | |

- **Each message is rendered in the recipient's language** from
  `config/notifications.yaml`. It carries a reference, the dates and, for a denial, the
  approver's comment: never a balance, never conversation text.
- **FR-23 reminders** (`scripts/send_reminders.py`): after 3 business days without a
  decision (company holidays not counted), or 2 days before the first day, the employee
  is told to check with HR in person. Once per round; nothing is escalated.

### 7. A permanent audit log

`audit_log` records requests, changes, cancellations, every approval decision, PIN and
password events, referrals sent and handled. Triggers reject `UPDATE`, `DELETE` and
`TRUNCATE` (FR-24). The writer refuses keys such as `text`, `message` and `reply`, so
nobody can put what an employee said into the permanent record by mistake.

### 8. The consented note to HR (FR-32)

```text
sensitive message ─► fixed reply + HR contact + "Would you like me to send your HR team
                     what you just wrote?"  ── YES ─► hr_referral + a notice to HR
                                            ── NO / anything else ─► nothing sent
```

- **Offered only** for the ten sensitive topics, to a signed-in employee, and not when
  the message asks to stay anonymous or mentions the hotline. Never in an emergency:
  the 911/988 reply stays alone.
- **Only a clear YES sends**, read by code, never the model (as for vacation).
- **What is sent** is the triggering message exactly as written (up to 1,000 characters),
  with the topic and language. The model never sees or rewrites it.
- **HR's Teams or email notice says nothing about it** ("an employee asked HR to contact
  them"). The words are read only in the console's Referrals page, by HR at the
  employee's location or an HR admin, never by the person who sent them.
- **Retention**: HR marks it handled; `scripts/delete_old_referrals.py` erases the words
  90 days later. The audit entry records the topic, never the words. Nothing ever goes to
  the Ethics Hotline, so hotline reports stay anonymous (handbook section 17).

### 9. Hours: an exception to FR-06, for HR only

| What | Kiosk | Supervisor | HR at the location, HR admin |
|---|---|---|---|
| Hours the request uses ("2 days, 16 hours") | ✅ | ✅ | ✅ |
| Hours left after this request | ❌ | ❌ | ✅ |

- **The hours a request uses are not a balance**: they come from the employee's shift (a
  holiday counts 0). The vacation tools now give them, and the prompt allows the model to
  repeat them exactly.
- **Hours left** is worked out as the vacation check does: available on the balance row
  for the request's year, minus the employee's other pending requests, minus this one.
- **Why HR only**: HR approves against the balance anyway and can see it in the HR
  system; a supervisor deciding on team coverage does not need it, and FR-06 exists so
  that balances are not spread further than necessary. The kiosk and the agent still
  never state a balance, and the own-data guardrail still routes "how many hours do I
  have left?" to HR.

## Why not the alternatives

| Alternative | Why not |
|---|---|
| Signed tokens (JWT) instead of database sessions | An idle limit and sign-out must take effect at once on a shared kiosk; a token cannot be revoked without a server-side list anyway. |
| A single-page app | More JavaScript and browser storage on a shared screen (NFR-06), for no gain: every page is a form or a list. |
| Streaming the reply token by token | Needs telemetry to watch for broken streams and partial replies; moved to Phase 9. |
| A model-written summary for HR | Could change or invent what was said in a sensitive matter. FR-32 asks for a summary; the employee's own short message is the most faithful one. |
| Putting the employee's words in the HR email or Teams message | Sensitive text would sit in mailboxes and chat history outside Roost's retention and access rules. |
| Entra ID sign-in now | Needs a Microsoft tenant and app registration; planned with the Teams work in Phase 12. |
| A shared rate-limit store (Redis) | One instance is enough for the MVP (NFR-02); a second instance needs it. |
| Showing balances to supervisors too | Not needed to decide on coverage, and widens who sees financial data. |

## How it was tested

828 tests (unit, and integration against real Postgres, each test inside a rolled-back
transaction). The ones that guard this phase:

| Area | What the tests prove |
|---|---|
| Kiosk (`test_kiosk_web.py`) | First sign-in allows only choosing a PIN; the cookie is stored only as a hash; 2 idle minutes end the session on the server; an htmx request after expiry replaces the whole screen; YES/NO buttons in the employee's language; kiosk notices shown once |
| Console (`test_console_web.py`) | Each approver sees only what is routed to them; HR only after the supervisor; forged decisions refused; 15-minute idle limit; a removed role ends access at once; hours for everyone, hours left for HR only; referrals readable only by the right HR, escaped, marked handled, words erased after 90 days |
| Approvals (`test_approvals.py`) | Rounds, order, authorization, self-approval, posting and giving back hours, CEO path, cancellation notices, FR-23 timing once per round |
| Security (`test_http_security.py`) | Every page needs sign-in and every form needs the CSRF token, both read from the app's own route lists so a new route fails until protected; security headers on every kind of response; no details on errors; an employee cannot reach a teammate's request, even with its reference code and an extra form field; a terminated employee is signed out at once; a new PIN or password replaces the token |
| Referral offer (`test_referral_flow.py`, `test_offer.py`) | Offered for every sensitive topic and nothing else; never for anonymity, emergencies or before sign-in; only a clear YES sends, and it sends the exact words |

The security fixes found while writing those tests (a terminated employee's kiosk
session kept working; a PIN change kept the old token) were each checked by undoing the
fix and watching its test fail.

Tried by hand on Windows: an employee booked days at the kiosk, the supervisor and HR
approved them in the console, and the outbox held the four notices in each recipient's
language (two in Spanish, two in English).

## Consequences

### Positive

- The vacation workflow is complete: request, confirm, two approvals, notices, reminders,
  and a balance that reflects what was approved.
- Every decision, its audit entry and its notices are one transaction.
- Approvers and HR work in their own language, like employees.
- An employee can ask HR to follow up without repeating a painful story, and HR gets
  their words without them landing in email.

### Negative and risks

- **Nothing is really delivered yet.** Email and Teams messages are JSON files until
  Phase 12. Reminders, delivery and the 90-day cleanup are scripts nobody schedules until
  Phase 11.
- **The rate limiter is in memory**, per process: fine for one instance, not for two.
- **Console passwords are one more credential** for HR to hand out and reset, until
  Entra ID replaces them.
- **Hours left is an exception to FR-06.** Tests pin it to HR, but every new page that
  shows a request must keep supervisors and the kiosk out of it.
- **A balance row stands in for a missing year.** Days in 2027 use the latest row up to
  2027 (here the 2026 row), as the vacation check does. If the HR system opens each year
  with a new row, approved hours will land where expected; if not, HR may be surprised.
- **The kiosk transcript drops the vacation confirmation question** once it is answered
  (the question lives in the pause, not in the history), so an employee scrolling back
  sees "submitted" without the question before it. Found in manual testing; the referral
  flow already keeps its question in the history. Fix with Phase 9's transcript work.
- **The anonymity check is a word list** in three languages; a request for anonymity in
  other words still gets the offer. Saying NO keeps it private, so the cost is one extra
  question.

### When to revisit

- Phase 9: token streaming; the transcript fix above; telemetry on sign-in failures,
  lockouts and referral counts (counts only, never text).
- Phase 11: schedule reminders, outbox delivery and the referral cleanup; move rate
  limits to a shared store before running a second instance.
- Phase 12: Entra ID for the console and Teams; real Teams and email delivery.
- If HR asks supervisors to see balances, that is a change to FR-06 and needs its own
  decision.
