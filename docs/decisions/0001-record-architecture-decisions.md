# 0001. Record architecture decisions as ADRs

Date: 2026-08-07

## Status

Accepted

## Context

This project makes a number of non-obvious, tradeoff-heavy decisions early
(access control model, retrieval strategy, model hosting, storage engines)
while running on tight constraints: a proof of concept (POC), a solo
developer, a slow internet connection, and a scope that will grow across
many future sessions/conversations. Decisions made now need to stay
legible to whoever (including a future version of the same developer)
picks the project back up without the original conversation as context.

## Decision

Every significant, hard-to-reverse architectural decision is recorded as an
Architecture Decision Record (ADR) — a short, numbered, immutable-once-accepted
markdown file under `docs/decisions/`, using the template at
[`template.md`](template.md). "Significant" means: it constrains future
options, it was chosen over a plausible alternative, or getting it wrong is
expensive to unwind.

Component-level implementation detail that doesn't rise to that bar belongs
in [low-level design docs](../design/) instead, which are expected to change
more often than ADRs.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| No written decision log; rely on git history and commit messages | Zero overhead | Git history explains *what* changed, rarely *why one option over another*; tradeoffs get lost |
| A single running `docs/DESIGN.md` | One file to check | Merges "decided and stable" with "still evolving" content; hard to tell what's settled |
| ADRs (chosen) | Cheap to write, one decision per file, immutable record of *why*, standard pattern | Discipline required to keep them one-decision-scoped; can go stale if superseded records aren't linked |

## Tradeoffs of the chosen option

ADRs are a process overhead: every non-trivial decision now requires a
written artifact, not just code. For a solo POC this is arguably more
ceremony than strictly necessary in the short term — the payoff is amortized
over the many future sessions this project will span, and is explicitly
requested by the project owner.

## Consequences

- New ADRs are numbered sequentially and never renumbered or edited
  destructively; superseding a decision means writing a new ADR that says so
  and updating the old one's `Status` line to point at it.
- The [README](../../README.md) and [architecture overview](../architecture.md)
  link into this directory as the canonical "why" behind the system.
