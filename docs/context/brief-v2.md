# V2 brief: from answering questions to doing work

> Captured on 2026-08-10, when V1's scope closed out. Same role as
> [the original brief](brief.md): the seed of truth that every V2
> [ADR](../decisions/) and [design doc](../design/) traces back to. Treat it
> as a historical record — if a decision here is revisited, update the ADR
> rather than editing this page.

## Problem statement

V1 answers questions. V2 does work: draft client emails, research market
trends on the open web, and process spreadsheets and internal databases.
Some of the internal data involved is highly sensitive.

## Why this is not "V1 plus some tools"

The three capabilities share a user experience. They do not share a safe
execution context.

| Capability | Reads | Produces or changes | Input trust | Worst realistic failure |
|---|---|---|---|---|
| Client email | CRM, account history, approved internal metrics | a draft, and optionally an irreversible external send | internal but permissioned; the user's own request can still be wrong | wrong recipient, confidential disclosure, a fabricated commitment or price |
| Market research | the open web, plus approved public briefs | an internal evidence report | **untrusted** — possibly adversarial, possibly stale | instructions embedded in fetched content, fabricated sources, data exfiltration |
| Spreadsheets and databases | structured internal data, potentially highly sensitive | a derived dataset or answer, optionally a controlled write | source is trusted; the user's intent and any generated query are not | unauthorised rows or columns, wrong aggregation, a runaway or mutating query |

The tempting design is one capable agent with all three tools available.
That design puts private data, attacker-controllable content and an
outbound channel in a single execution context. Any one of the three is
manageable; together they compose into a system where a single successful
injection can read anything and send it anywhere.

So the organising constraint for V2 is: **no execution context holds all
three.** Everything below follows from that.

## The core object: a Run

An agent is a behaviour. A **Run** is the durable, auditable execution of
one user intent, and it is the thing the system is actually built around.
It carries caller and tenant identity; delegated scopes and declared
purpose; task type, risk tier and data-classification ceiling; the plan,
prompt, model, retrieval and policy versions in play; step, token, time and
spend budgets; checkpoints and evidence references; proposed, approved and
committed effects; and a terminal state with a stated reason.

A model may *propose* a plan or a tool call. Code owns transitions,
budgets, approvals, retries and outcomes.

The invariant this exists to support: **authority is carried, never
generated.** A model can choose among capabilities a Run already holds. It
cannot add a scope, a recipient, a destination, a data class or an effect
class. Effective permission at any moment is an intersection —

```
caller permission ∩ tenant policy ∩ Run purpose and scopes
                  ∩ tool policy ∩ data classification
```

— re-evaluated at every capability call, and again before any external
effect.

## V2 scope

- Corporate-authenticated internal users (still mock-authenticated, see
  [ADR-0007](../decisions/0007-mock-identity-and-group-lookup.md)).
- Email **drafting**, with send available only under explicit policy and an
  approval bound to the exact payload.
- Permission-aware retrieval over approved documents and CRM fields —
  building on V1's ACL enforcement, not replacing it.
- Read-only typed analytics over certified metrics for spreadsheet and
  database questions.
- Web research in an execution context holding no internal data and no
  ability to send anything.
- Evidence bundles: source identifiers, timestamps, and provenance for every
  computed number.
- Per-Run budgets for steps, tool calls, tokens, elapsed time and spend.
- Offline evaluation, tracing, audit and release gates covering all of it.

## Explicitly out of scope, and what would change that

| Excluded | Why | What would let it in |
|---|---|---|
| Unrestricted natural-language-to-SQL | Schema, business semantics and row/column authorization make correctness workload-specific; a query can execute perfectly and still use the wrong join, unit, cohort or date | A representative golden workload passing *semantic* checks, not just execution — plus a read-only sandbox, caller-enforced permissions, statement/cost/time limits and a working abstain path |
| Autonomous send for all email | Send is irreversible and trust is currently unmeasured | A risk classifier hitting an agreed recall on high-risk cases, a measured escape rate on low-risk cohorts, and a verified kill switch and rollback |
| One agent holding every capability | Combines untrusted content, sensitive data and external egress | No default path back. Any exception needs a threat-model review and hard information-flow constraints |
| A general browser or navigation agent | Broad web actions widen the injection and egress surface substantially | A narrow business need that allowlisted fetch and search genuinely cannot serve, and that passes security testing |
| Cross-user learning from raw interaction data | Privacy and retention questions are unresolved | Explicit purpose and consent, a de-identification assessment, isolation and a retention policy |
| Fine-tuning on internal content | Deletion, provenance and lifecycle burden buy nothing here yet | Behaviour that prompting and retrieval demonstrably cannot reach, with an approved training-data and deletion path |

## Delivery shape

Interfaces and mocks first, working implementations second. Every seam gets
its contract and a fixture implementation before any phase fills it in, so
later phases add behaviour rather than restructuring boundaries. Phases:

0. Contracts, registry, gateway, policy engine, Run state machine, mocks.
1. Run service: durable store, budgets, `POST /runs`; V1's question path
   becomes one task template among several.
2. Gateway wired live, with the negative authorization tests as the
   deliverable.
3. Client email: effect ledger, payload-bound approval, idempotent outbox.
4. Market research: zone isolation and an injection red-team corpus.
5. Analytics: certified metrics over a fixture warehouse.
6. A protocol server exposing the registry to external clients.

## Constraints carried over from V1

Local models via Ollama, containers via OrbStack, small default models that
work on a poor connection, and documented alternatives rather than
unexplained picks. None of that changes.
