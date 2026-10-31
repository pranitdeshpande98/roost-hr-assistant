# ADR-0001: Record architecture decisions

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-31 |
| Requirements | NFR-12 |

## Context

Roost is built to production standards, and many of its design choices involve
trade-offs (framework, model provider, data stores). Without a written record,
the reasoning behind each choice is lost, and later changes risk breaking
assumptions nobody remembers.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| No formal record | No overhead | Reasoning is lost; decisions get re-debated |
| Wiki or external doc | Easy to edit | Drifts from the code; not versioned with it |
| ADRs in the repo | Versioned with code; reviewed in pull requests | Small writing overhead per decision |

## Decision

Record each significant architecture decision as a numbered Markdown file in
`docs/adr/`, using `template.md`. Accepted ADRs are not edited; a changed
decision gets a new ADR that supersedes the old one.

## Consequences

- Every significant choice has a written rationale reviewers can find.
- Pull requests that change architecture must include or update an ADR.
- The decision table in `docs/02-architecture.md` is kept in sync with `docs/adr/`.
