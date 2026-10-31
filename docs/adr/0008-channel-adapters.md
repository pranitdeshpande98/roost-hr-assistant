# ADR-0008: Channel adapters with server-rendered kiosk and approver console

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | FR-03, FR-21, FR-22, FR-23, FR-40, FR-42, NFR-01, NFR-06, NFR-07 |

## Context

Roost is used through three channels: a shared plant kiosk (MVP), an approver
console for supervisors and HR (MVP), and Microsoft Teams (P1). The kiosk is a
shared public device: nothing about a user may remain after sign-out (NFR-06),
sessions end after 2 minutes idle (FR-40), and the UI must be touch-friendly
and accessible (NFR-07). Answers should stream to meet the 3 s first-token
target (NFR-01). Teams requires a Microsoft 365 tenant that may not be
available during development.

## Options considered

### Channel integration

| Option | Pros | Cons |
|---|---|---|
| Channel logic inside the agent | Fewer files | Every new channel changes the agent; hard to test |
| **Adapters around a channel-agnostic agent** | Agent, tools and rules are unaware of channels; Teams added without touching the agent | One translation layer per channel |

### Kiosk and approver console UI

| Option | Pros | Cons |
|---|---|---|
| React single-page app | Rich interactivity | Second language and build pipeline; data tends to live in browser state |
| Streamlit / Gradio | Very fast to build | Limited control over sessions, timeouts and security; not suited to a public kiosk |
| **FastAPI + Jinja2 templates + HTMX** | One language and deployment; data stays server-side; simple, accessible HTML | Less suited to highly interactive interfaces (not needed) |

## Decision

1. The agent accepts a channel-agnostic `ChannelMessage` and returns a
   `ChannelReply` (Pydantic models). Each channel has an adapter in
   `src/roost/adapters/` that translates to and from these models.
2. **Kiosk and approver console** are server-rendered with FastAPI, Jinja2
   templates and HTMX. Answers stream to the browser with Server-Sent Events.
3. **Kiosk sessions** are server-side, identified by a secure, HTTP-only,
   same-site cookie. The 2-minute idle timeout is enforced by the server; a
   client-side timer only clears the screen. No employee data is written to
   browser storage. Sign-out destroys the server session.
4. **Approver console login (MVP):** local accounts with Argon2-hashed
   passwords, behind an authentication interface designed for Microsoft
   Entra ID single sign-on (P1).
5. **Notifications** go through a `NotificationSender` interface with email and
   Teams implementations. The MVP email implementation writes to a local
   outbox; no real email is sent. The channel per employee follows FR-22.
6. **Teams (P1)** is implemented as an adapter using the Microsoft Teams SDK
   for Python, with adaptive cards for approvals. The SDK choice is re-checked
   when that phase starts.

## Consequences

- Adding or replacing a channel never changes the agent, tools or business rules.
- The shared-kiosk requirement is enforced by design: there is no client-side
  state to leak between employees.
- The whole MVP (API, kiosk, console) ships as one Python service (ADR-0002).
- The kiosk device itself must run a locked-down browser in kiosk mode; this
  is an operational requirement documented for deployment.
- When Entra ID SSO and Teams arrive (P1), they plug into existing interfaces.
