# 0011. Make a durable Run the core execution object

Date: 2026-08-10

## Status

Accepted

## Context

V1's execution model is a function call. `answer_question()` takes a
question, returns an answer, and holds nothing afterwards. That is exactly
right for a single-shot question: the whole interaction fits in one
synchronous call stack, and there is nothing to recover.

[The V2 brief](../context/brief-v2.md) breaks all three of those
assumptions. Drafting a client email involves a human approval step that
may arrive minutes later. Web research runs long enough to need budgets and
cancellation. Anything that sends an email has an outcome that must survive
the process that produced it, because "did this already send?" is a
question the system will genuinely have to answer after a crash.

So V2 needs some object that outlives a request. The question is what it
is, and how much it owns.

## Decision

Make a **Run** — the durable, auditable execution of one user intent — the
core domain object, and give it ownership of authority, budget and state
transitions.

Concretely: a `Run` carries the caller's identity, tenant, purpose, granted
scopes, allowed zones and classification ceiling; a `RunStatus` state
machine governs every status change; and a `RunStore` persists it. A model
may propose; code transitions.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Keep the request/response function shape and add a session cache | Smallest change; nothing new to persist; V1's pipeline stays untouched | A cache has no state machine, so "approved but not yet sent" is not representable; nothing distinguishes "never attempted" from "attempted, outcome unknown", which is precisely the distinction an irreversible send needs |
| Adopt a durable-workflow engine (Temporal, Airflow, or similar) | Real durability, retries, timers and cancellation, all battle-tested; would genuinely earn its place at production scale | Brings an external service and a programming model to a project whose stated constraint is running locally on a small machine; more importantly, the interesting content here is *authority* semantics (scopes, zones, ceilings, effect classes), which no engine supplies — they would have to be built on top anyway |
| A durable Run aggregate, hand-written (chosen) | Authority, budget and transition rules are explicit and directly testable; no new infrastructure; the state machine's exceptions (see below) are visible in one table | Recovery, timers and scheduling have to be written rather than inherited; the in-memory store is not durable, so Phase 1 owes a real one |

## Tradeoffs of the chosen option

The honest cost is that this reimplements a slice of what a workflow engine
gives for free, and does it less well: there is no timer service, no
distributed scheduling, and the first implementation of `RunStore` is an
in-memory dict. If this system ever ran real traffic, adopting an engine
underneath the Run aggregate would be a reasonable next step — and the
aggregate is deliberately shaped so that it could be, since it holds no
infrastructure of its own.

A second cost is ergonomic. `Run` is a frozen dataclass, so every mutation
returns a new instance and callers must thread it through. That is
genuinely more annoying than mutating in place, and it is the point: if a
workflow step could widen `approved_scopes` in place, then
[ADR-0012](0012-capability-registry-and-gateway.md)'s central invariant
would hold only by everyone remembering not to. Making the aggregate
immutable turns a discipline into a type error.

## Consequences

- Two divergences from a naive reading of the state machine are recorded in
  `runs/machine.py` and worth knowing about:
  - `FAILED_RETRYABLE` is treated as a **halt, not a terminal state**. Making
    it terminal would mean a transient provider error could only be
    recovered by starting a new Run, discarding the checkpoint and effect
    ledger that make recovery safe in the first place.
  - `COMMITTING_EFFECT` **cannot** transition to `CANCELLED` or
    `BUDGET_EXHAUSTED`. Once an irreversible effect is in flight the
    outcome is *unknown*, not absent, and "cancelled" would assert
    something nothing at that layer knows.
- Terminal states other than `COMPLETED` require a stated reason, enforced
  rather than defaulted. A corpus of Runs that ended for "unknown" reasons
  cannot distinguish a policy rejection from a provider outage, which is
  the distinction operational review needs most.
- Phase 1 owes a durable `RunStore`. The in-memory mock already implements
  the optimistic-concurrency check the real one will, so callers are not
  written against a more permissive contract than they will get.
- V1's `orchestration/pipeline.py` is untouched by this ADR and keeps
  working. Phase 1 re-expresses it as one task template among several
  rather than rewriting it.
