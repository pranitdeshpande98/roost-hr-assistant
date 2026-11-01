# Roost HR Assistant: Product Requirements

> Hollow Creek Poultry Co. is a fictional company. All policies and data are synthetic.

| Field | Value |
|---|---|
| Status | Approved v1.0 |
| Last updated | 2026-10-31 |
| Business rules | [00-business-rules.md](00-business-rules.md) |
| Policy source | Hollow Creek Poultry Co. Employee Handbook v1.0 (effective 2026-11-01) |

---

## 1. Problem statement

Hollow Creek Poultry Co. has ~4,500 employees across three processing plants
(Georgia, Arkansas, North Carolina) and a corporate office (Texas). Today:

- Vacation requests go through a Microsoft Power Apps form keyed on employee number.
- Employees then email HR with follow-up questions. The most common: whether unused
  vacation carries over, and payroll issues.
- Policy answers are buried in a long English-only handbook, while much of the plant
  workforce reads Spanish or Vietnamese first.
- Plant employees work fixed shifts, including overnight sanitation, when HR is closed.
- Drivers have no company email or Teams account.

**Goal:** A multilingual assistant (Roost), available 24/7 at plant kiosks and in
Microsoft Teams, that answers handbook questions with citations, handles vacation
requests end-to-end with deterministic rule validation and two-step approval,
and routes sensitive matters to the right HR team or the ethics hotline.

## 2. Personas

| ID | Persona | Language | Channel | Primary needs |
|---|---|---|---|---|
| P1 | Production line worker, 2nd shift | Spanish | Teams (phone), kiosk | Request vacation, ask about attendance points, paydays |
| P2 | Sanitation worker, overnight | Vietnamese | Kiosk | Ask questions when HR is closed, request vacation |
| P3 | Driver | English | Kiosk only (no email/Teams) | Request vacation, check request status |
| P4 | Corporate employee (Biweekly) | English | Teams | Benefits eligibility, holiday schedule, vacation |
| P5 | Supervisor / manager | English | Teams | Approve or deny requests quickly |
| P6 | Plant or corporate HR generalist | English | Teams | Second approval, receive consented case summaries, reset kiosk PINs |
| P7 | HR admin | English | Admin console | Publish handbook versions, review conversation logs |

## 3. Scope

| In MVP (P0) | Later (P1) | Out of scope |
|---|---|---|
| Handbook Q&A with citations (EN/ES/VI) | Microsoft Teams channel (employees + approvers) | Shift swaps |
| Vacation request: create, change, cancel, status | Teams single sign-on | Showing any balance, pay, deductions, bank data, elections or point totals |
| Deterministic rule validation | HR admin conversation-log viewer | Eligibility decisions (FMLA, accommodations) |
| Two-step approval routing | Feedback (thumbs up/down) analytics | Legal advice |
| Approver web console | Spanish/Vietnamese handbook translations as sources | Payroll processing or corrections |
| Kiosk web UI with employee number + hardened 6-digit PIN | | Real HRIS / Power Apps integration (mocked behind an adapter) |
| Sensitive-topic routing + consented HR summary | | |
| Email notifications (mocked provider) | | |
| Handbook ingestion + versioning | | |

**Why Teams is P1:** building for Teams requires a Microsoft 365 tenant that may not
be available on a free tier. The kiosk UI and approver console cover every MVP flow,
and the channel layer is designed so Teams plugs in without changing the agent.

## 4. Functional requirements

### 4.1 Knowledge and conversation

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Answer handbook questions using only retrieved handbook text, citing section number, handbook version and effective date. | P0 |
| FR-02 | When the handbook does not answer a question, say so and give the employee's HR contact. Never guess. | P0 |
| FR-03 | Reply in English, Spanish or Vietnamese. Kiosk: language chosen on the start screen. Teams: detected from the message, switchable on request. Non-English answers state the English handbook is binding. | P0 |
| FR-04 | Every reply ends with the disclaimer footer (informational only; HR decisions are final). | P0 |
| FR-05 | Answer payroll schedule questions (pay period, payday, holiday payday rule) based on the employee's payroll class. | P0 |
| FR-06 | Never disclose pay, vacation or sick balances, attendance point totals, deductions, benefit elections or bank data. Decline and refer to HR. | P0 |
| FR-07 | Redirect out-of-scope requests (e.g. shift swaps) to the right person per the handbook. | P0 |

### 4.2 Vacation requests

| ID | Requirement | Priority |
|---|---|---|
| FR-10 | Collect dates and half/full day, validate against rules V-01 to V-09, show a summary, and submit only after explicit employee confirmation. Return a request reference ID. | P0 |
| FR-11 | When validation fails, explain which rule failed in plain language (citing the handbook section) without revealing balances. For V-06, suggest the nearest available dates. | P0 |
| FR-12 | Employees can view the status of their own requests (pending, approved, denied, cancelled). | P0 |
| FR-13 | Employees can change a request at any time before it starts; a change restarts both approvals. | P0 |
| FR-14 | Employees can cancel a request before its first day; approvers are notified. | P0 |

### 4.3 Validation rules (deterministic code, never the LLM)

| ID | Rule | Handbook |
|---|---|---|
| V-01 | Employee has completed the 90-day introductory period. | 6.2 |
| V-02 | First day off is at least 14 calendar days away. | 6.4 |
| V-03 | Each day is a half day or full day of the employee's scheduled shift. | 6.3 |
| V-04 | Dates are in the future and do not overlap an existing active request. | 7.1 |
| V-05 | Weekly Production: no dates between Nov 15 and Dec 31. | 6.5 |
| V-06 | The employee's department, at their location and on their shift, stays at or below 15% off on every requested day. | 6.6 |
| V-07 | No more than 10 consecutive scheduled workdays. | 6.7 |
| V-08 | Available vacation minus pending requests covers the request (checked silently). | 6.8 |
| V-09 | Holidays inside the range are not charged as vacation. | 12.5 |

### 4.4 Approvals and notifications

| ID | Requirement | Priority |
|---|---|---|
| FR-20 | Route approvals: plant employees → supervisor/manager → plant HR; corporate → supervisor → Corporate HR; CEO → notification to Corporate HR only. | P0 |
| FR-21 | Approvers approve or deny with an optional comment. P0: approver web console. P1: Teams card. | P0 / P1 |
| FR-22 | Notify the employee of decisions: Teams if available, else email; drivers see status at the next kiosk sign-in. | P0 |
| FR-23 | If no decision within 3 business days or 2 days before the start date (whichever first), email the employee to check with HR in person; drivers get a kiosk notice. No automatic escalation. | P0 |
| FR-24 | Record every request, change, approval, denial and cancellation in a permanent, append-only audit log. | P0 |

### 4.5 Sensitive matters

| ID | Requirement | Priority |
|---|---|---|
| FR-30 | Detect sensitive topics: harassment, discrimination, retaliation, violence/threats, injury, safety hazards, medical/accommodation, immigration, wage disputes, personal crisis. Give no substantive answer beyond handbook reporting guidance. | P0 |
| FR-31 | Provide the employee's HR team contact and the ethics hotline. Emergencies (danger, crisis): 911 / 988 and plant safety first. | P0 |
| FR-32 | Offer to send HR a short case summary; send only after explicit consent. Never forward ethics-hotline matters. | P0 |

### 4.6 Identity, access and administration

| ID | Requirement | Priority |
|---|---|---|
| FR-40 | Kiosk sign-in with employee number + 6-digit PIN. Reject common patterns (e.g. 000000, 123456, repeated or sequential digits) and the employee's birth date. Lock the account for 15 minutes after 5 failed attempts. Rate-limit sign-in attempts per kiosk. Sign out after 2 minutes idle and clear the screen. | P0 |
| FR-41 | PINs are stored only as Argon2 hashes. Plant HR sets and resets PINs in person; the employee must choose a new PIN at first sign-in. | P0 |
| FR-42 | Teams users identified by their Microsoft account (SSO). | P1 |
| FR-43 | Role-based access: employee, supervisor, hr_generalist, hr_admin. Employees see only their own requests; approvers see only requests routed to them. | P0 |
| FR-44 | HR admins publish handbook versions; publishing re-indexes content and retires the previous version from answers. | P0 |
| FR-45 | Conversation logs are deleted automatically after 45 days. | P0 |
| FR-46 | HR admins can view conversation logs within the 45-day window. | P1 |
| FR-47 | Employees can rate answers (thumbs up/down). | P1 |

### 4.7 Records, observability and quality signals

Four separate stores, each with its own purpose, retention and access:

| Store | Contents | Retention | Access |
|---|---|---|---|
| Conversation transcripts | Full messages and cited sections | 45 days (FR-45), unless under legal hold (FR-48) | HR admins |
| Audit log | Actions only: requests, approvals, denials, cancellations, HR summaries sent, PIN resets, legal holds | Permanent, append-only (FR-24) | HR, auditors |
| Traces and telemetry | Latency, tokens, cost, tools called, guardrail decisions, errors, fallbacks; PII redacted | 30 days | Engineering |
| Quality signals | Ratings, "I don't know" answers, guardrail blocks, fallbacks, failed tool validations | Aggregated | Engineering, HR |

| ID | Requirement | Priority |
|---|---|---|
| FR-48 | HR admins can place a legal hold on a conversation, which pauses its 45-day deletion until released. Placing and releasing a hold is recorded in the audit log. | P0 |
| FR-49 | Sensitive-topic content (FR-30) is stored only in the transcript. Traces, dashboards and analytics record only the sensitive-topic category, never the message content. Sensitive conversations are never added to eval datasets. | P0 |
| FR-50 | A production conversation becomes an eval case only after PII is removed and an HR admin approves it. | P1 |
| FR-51 | Track production quality signals: "I don't know" rate by topic, ratings by language, tool-validation failures, fallback-provider usage, and guardrail triggers by category. | P1 |

## 5. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Latency | p95 time to first token ≤ 3 s; p95 complete answer ≤ 10 s; rule validation ≤ 300 ms. |
| NFR-02 | Scale | 4,500 employees; design for 100 concurrent sessions and 2 requests/s sustained, with peaks at shift changes (6:00 a.m., 2:30 p.m., 3:00 p.m., 11:30 p.m.). |
| NFR-03 | Availability | Production design target 99.5% monthly. Free-tier demo deployment is best effort (documented). |
| NFR-04 | Security | OWASP Top 10 and OWASP Top 10 for LLM Applications addressed; least-privilege tools; secrets only in environment variables or a secret store. |
| NFR-05 | Privacy | PII redacted from logs and traces; LLM receives only the minimum data needed (internal IDs, never PINs); no real employee data anywhere. |
| NFR-06 | Shared kiosk | Nothing about a user remains on screen or in browser storage after sign-out. |
| NFR-07 | Accessibility | WCAG 2.1 AA; touch-friendly kiosk UI; plain-language answers (about grade 6–8 reading level). |
| NFR-08 | Cost | $0 infrastructure using free tiers; token usage and estimated cost tracked per conversation. |
| NFR-09 | Portability | LLM, embedding, vector store, email and HR system behind interfaces; swapping a provider is a configuration change. |
| NFR-10 | Resilience | Fallback LLM provider; if all LLMs fail, return a fixed message with HR contacts; vacation tools never depend on the LLM to validate. |
| NFR-11 | Observability | Every turn traced: retrieval, tool calls, guardrail decisions, latency, tokens, cost. |
| NFR-12 | Code quality | Type-checked, linted, ≥ 80% unit-test coverage on core packages; CI blocks merges on failure. |

## 6. Success metrics

### 6.1 Business outcomes

| Metric | Target |
|---|---|
| Policy questions resolved without contacting HR (excluding mandatory referrals) | ≥ 60% |
| Median time to submit a vacation request | < 2 minutes (baseline ~10 minutes incl. follow-up email) |
| Reduction in HR emails about carryover and paydays | ≥ 50% |
| Answer helpfulness (thumbs up share) | ≥ 80% |

### 6.2 Quality gates (enforced by evals in CI)

| Metric | Threshold |
|---|---|
| Answer faithfulness to retrieved handbook text | ≥ 0.90 |
| Citation correctness (cited section supports the answer) | ≥ 0.90 |
| Retrieval hit rate @5 | ≥ 0.90 |
| Sensitive-topic routing recall | ≥ 0.98 |
| Sensitive-topic routing precision | ≥ 0.85 |
| Tool-call correctness (right tool, right arguments) | ≥ 0.95 |
| Restricted data disclosure (FR-06) | 0 cases |
| Disclaimer present | 100% |
| Reply language matches the user's language | ≥ 0.98 |
| Prompt-injection attempts blocked | ≥ 0.95 |
| Validation rules V-01 to V-09 | 100% (unit tests) |

## 7. Assumptions and constraints

- Synthetic data only; the project is public on GitHub.
- Development on Windows 11 without a GPU; LLM inference via free-tier hosted open-weight models.
- Free-tier rate limits are far below production load; the design must make upgrading a configuration change.
- No real HRIS, Power Apps, or email system: each is replaced by a mock behind an adapter.
- The English handbook is the only authoritative source in the MVP.

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Incorrect or invented policy answer | Legal and trust damage | Answer only from retrieved text, cite sections, faithfulness evals as CI gates, "I don't know" path |
| Sensitive matter handled by the bot | Legal exposure, harm to employee | Guardrail classifier before the LLM, high-recall threshold, fixed routing responses |
| Restricted data leaked | Privacy violation | Tools never return balances to the LLM; output filter; zero-tolerance eval |
| Prompt injection via user input or documents | Policy bypass | Input guardrails, tool allow-lists, server-side authorization on every tool |
| Free-tier rate limits or outages | Degraded service | Provider fallback, caching, graceful-degradation message |
| No access to a Microsoft 365 tenant | Teams channel delayed | Channel adapter design; kiosk + approver console cover the MVP |
| Weak Vietnamese/Spanish quality | Poor experience for plant workers | Multilingual embeddings, language-specific eval sets |
| Sensitive disclosures leak into logs, dashboards or test data | Privacy violation, loss of employee trust | Category-only telemetry (FR-49), PII redaction (NFR-05), reviewed eval promotion (FR-50) |
| Coworker knows an employee number and guesses the PIN | Someone acts as another employee | 6-digit PIN, common-PIN blocklist, lockout, per-kiosk rate limit, confirmation step, audit log |

## 9. Traceability

Requirement IDs (FR, NFR, V) are referenced in test names, eval cases, ADRs and
commit messages so every behavior can be traced to an approved requirement.
For example, `test_v02_rejects_less_than_14_days_notice` tests rule V-02.
