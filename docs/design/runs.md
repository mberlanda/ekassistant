# Low-level design: Run Orchestration

Reference spine item c, at V2 scope. Where
[orchestration.md](orchestration.md) describes V1's single-shot question
pipeline, this describes the durable execution object that V2 workflows run
inside. Both exist: V1's pipeline keeps working, unchanged, and
`knowledge_qa` (Phase 1) is one task template that happens to call it
internally rather than a replacement for it.

See [ADR-0011](../decisions/0011-durable-run-aggregate.md) for why a Run
exists at all, and [ADR-0014](../decisions/0014-deterministic-workflow-templates.md)
for why workflows are templates rather than a planner loop.

**Status:** the record, intake, the state machine and budgets described
below are built and tested since Phase 0. Persistence (`SqliteRunStore`)
and the workflow-execution driver (`runs/service.py`, which is what
actually wires budgets to transitions) are built as of Phase 1, along with
the first real template, `knowledge_qa`. Still designed-only: the effect
ledger's durable store (Phase 3), a timer service for `deadline_ts`
(nothing yet), and the `delegation_ref` seam.

## The record

`runs/aggregate.py`. Field groups, and what each is for:

| Group | Fields | Purpose |
|---|---|---|
| Identity | `run_id`, `tenant_id`, `principal_id` | Stable execution, tenancy, caller |
| Authority | `purpose`, `approved_scopes`, `allowed_zones`, `classification_ceiling` | What the capability gateway intersects against |
| Routing | `task_type`, `risk_tier` | Which template, how much validation |
| Replay | `plan_version`, `prompt_version`, `model_route`, `policy_version` | Comparison, rollback, "would this still be allowed?" |
| Progress | `status`, `terminal_reason`, `checkpoint`, `step_results` | Recovery and operational measurement |
| Lineage | `input_refs`, `evidence_refs`, `effect_intent_ids` | Provenance without duplicating payloads |
| Governance | `budget`, `spend` | Termination and cost |

### What a Run does not store

No question text beyond `input_refs`, no retrieved chunk content, no draft
body. Those live in their own stores and are referenced.

The reason is not size. A Run record is read by status polling, operations
tooling and audit review — a much wider audience than the content itself
has. Copying sensitive payloads into it would recreate, in a
less-guarded place, exactly the data-protection problem the rest of the
design is spent avoiding. `StepResult` carries an `evidence_ref`, not
evidence.

## Intake

`runs/intake.py`, and it is the **only** constructor that derives
authority.

```
IntakeRequest(principal, groups, purpose, task_type)
        │
        ├─ policy.profile_for(purpose)   → allowed_zones, ceiling, effect classes
        └─ policy.scopes_for_groups(...) → approved_scopes
        │
        ▼
     Run(...)  ← authority fixed here, and only narrowable afterwards
```

Note the signature: groups and purpose go *in*, scopes and zones come
*out*. `IntakeRequest` has no `approved_scopes` field, and
`test_run_intake.py` asserts structurally that it never grows one — a
caller able to supply authority directly would make every downstream check
decorative.

The same person gets different Runs for different purposes. A user holding
`email.send` as a group grant who declares a research purpose gets a Run
that still carries the scope but not the `EFFECT` zone, so the scope is
unusable. That is the intersection doing its job, and it is why one person
does not need two accounts.

Unknown purpose or a task type the purpose does not cover: `IntakeRejected`,
carrying the `PolicyDecision` so the caller can surface both the reason and
the policy version.

## State machine

`runs/machine.py`. Happy path:

```
RECEIVED → CLARIFYING → PLANNED → RUNNING → VALIDATING
         → AWAITING_APPROVAL → COMMITTING_EFFECT → COMPLETED
```

`CLARIFYING`, `AWAITING_APPROVAL` and `COMMITTING_EFFECT` are skippable;
a read-only Run goes `VALIDATING → COMPLETED` directly. Terminal
alternatives: `REJECTED_POLICY`, `FAILED_TERMINAL`, `CANCELLED`,
`BUDGET_EXHAUSTED`, `TIMED_OUT`.

The transition table is written out per state rather than derived from a
rule, because the interesting content is the exceptions:

- **There is no `RUNNING → COMPLETED` shortcut.** Output always passes
  through validation. A fast path here would skip every egress check.
- **`VALIDATING → RUNNING`** is the bounded repair loop. The step budget
  stops it looping, not the state machine.
- **`AWAITING_APPROVAL → RUNNING`** is how an edit invalidates an approval:
  the reviewer changed the payload, the approved hash no longer matches, so
  the Run goes back to work instead of committing something nobody
  approved.
- **`COMMITTING_EFFECT` cannot be cancelled or budget-exhausted.** Once an
  irreversible effect is in flight the outcome is unknown, not absent. The
  only honest exits are success, a retryable failure that must reconcile
  first, a terminal failure, or a timeout that still owes reconciliation.
- **`FAILED_RETRYABLE` is a halt, not an ending.** It is the resume point.
  Treating it as terminal would mean a transient provider error could only
  be recovered by a new Run, discarding the checkpoint and effect ledger
  that make recovery safe.

Terminal states other than `COMPLETED` require a reason. Enforced, not
defaulted: a population of Runs that ended for "unknown" reasons cannot
distinguish a policy rejection from an outage.

## Budgets

`Budget` sets limits, `BudgetSpend` records usage, and the two are kept
apart so they can be compared and logged independently.

`would_exhaust()` checks and returns the *name* of the dimension that would
break; `charge()` records and does not enforce. The split is deliberate:
the enforcement decision belongs to whoever knows what to do about it (fail
the step, return a partial result, transition to `BUDGET_EXHAUSTED`), not
to the accounting. It also means the gateway can check *before* invoking an
irreversible capability, rather than discovering the budget is gone after
the email has left.

`deadline_ts` is absolute wall-clock rather than a duration, because a Run
can be checkpointed and resumed minutes later and a duration would silently
reset the clock on every resume.

Denied capability attempts still consume budget. A model looping on a
forbidden tool must run out rather than loop for free.

**Wired to transitions since Phase 1** (`runs/service.py`'s
`charge_or_exhaust()`): Phase 0 shipped `would_exhaust()` (reports) and the
state machine's terminal-state-with-reason vocabulary (enforces), but
nothing called one from the other, so a Run that exhausted its budget kept
being handed to the next step anyway. `charge_or_exhaust()` is the missing
call: it charges when the budget allows it, and otherwise transitions the
Run to a terminal state with a reason naming the exhausted dimension. One
mapping choice is not obvious from `would_exhaust()`'s return value alone:
its "deadline" dimension routes to `TIMED_OUT`, every other dimension
(`max_steps`, `max_tokens`, `max_spend_micros`) routes to
`BUDGET_EXHAUSTED` — both are legitimate terminal reasons and a corpus of
stopped Runs needs "stalled" distinguishable from "did a lot of work".
`runs/service.py::execute_workflow()` calls `charge_or_exhaust()` once
before every workflow step, so a step's own capability calls (which share
the same `spend.steps` counter via `gateway.invoke()`) are caught by the
*next* step's pre-check rather than needing a second enforcement path for
"tool calls" specifically.

## Persistence

`RunStore` is a four-method Protocol — create, get, save, list. Small on
purpose: a Run is one aggregate written as a whole snapshot, and
field-level update methods would let two concurrent writers each persist a
partial view and lose the other's change.

That is not theoretical here. Runs are long-lived and touched from more
than one place — a workflow step, an approval callback, a cancellation —
so `save()` performs an optimistic-concurrency check and raises
`ConcurrentRunUpdate` on a stale write. An approval silently overwritten by
an in-flight step is how something unapproved gets committed.

`InMemoryRunStore` is the Phase 0 mock and implements that check too. A
mock that accepted writes the real store would reject teaches callers the
wrong contract.

**Built since Phase 1:** `runs/sqlite_store.py`'s `SqliteRunStore`
implements the same Protocol and the same optimistic-concurrency contract
(`ConcurrentRunUpdate` on a stale save or a duplicate create,
`RunNotFound` on a missing get) — nothing InMemoryRunStore rejects is
accepted here. The `save()` compare-and-swap is one `UPDATE ... WHERE
updated_at <= ?` statement, not a read followed by a write, so two writers
racing on the same stale copy cannot both observe "not stale yet" before
either commits.

Worth being precise about what that check actually guarantees, because it
is easy to over-read: it compares wall-clock timestamps
(`stored.updated_at <= incoming.updated_at`), not a version token tied to
what the caller actually read. `charge()` always stamps the current time,
and time only moves forward, so two callers that both read the same stale
snapshot and then independently charge it can *both* legitimately pass
this check — a real, inherited lost-update gap, not something introduced
by the durable store (`InMemoryRunStore` has the identical comparison; see
`test_run_store_sqlite.py`'s `test_concurrent_saves_never_cross_thread_error_or_corrupt`,
which documents this rather than asserting a guarantee neither store
actually gives). What the check *does* reliably catch is the scenario
`test_save_rejects_a_stale_copy` pins for both stores: a caller holding
onto an old copy for a while (an approval callback, say) saving over a
newer one that landed in the meantime.

The other thing worth knowing: unlike `SqliteKeywordIndex` (see
[retrieval.md](retrieval.md) and PR #6's real, reproduced concurrency
bug), `SqliteRunStore` does not need to be constructed inside a route
handler's function body to be thread-safe. It never holds a sqlite3
connection across calls — every method opens, uses and closes its own —
so construction and use are always on the same thread regardless of which
OS thread FastAPI's dependency resolution lands on. A `SqliteRunStore`
instance is therefore safe to cache as a singleton
(`api/main.py::get_run_store`, `@lru_cache`'d), which the keyword index is
specifically not.

## Workflows

`workflows/base.py`. A `Workflow` declares `task_type`, `version`,
`required_zones`, `required_scopes` and an ordered `steps()`.

`WorkflowContext` is deliberately spare — a step gets the Run, the gateway
and the ledger, and nothing else. No settings, no database handle, no HTTP
client, no model client of its own. Every external touch therefore goes
through the gateway as an authorized, audited contract invocation, and a
step that needs something the gateway cannot provide needs a capability,
not a shortcut.

`StepOutcome` returns the Run rather than mutating it, so a step that drops
the budget charge is a visible mistake at the call site. It may
*request* a status transition (`request_status`) but cannot perform one —
a validator that spots an external recipient asks for `AWAITING_APPROVAL`,
and the Run service decides whether that is legal from the current state.

`check_sufficiency()` runs before execution and reports **every** shortfall
at once, not the first. A workflow that would be denied on its third step
should never have started, especially once a reversible write has landed;
and a user being told about one missing scope at a time cannot work out
that the purpose was simply the wrong one.

### The Run service (Phase 1): `runs/service.py`

`execute_workflow(run, workflow, store, gateway, ledger)` is the generic
driver every task template runs through: pre-flight `check_sufficiency()`,
`RECEIVED → PLANNED → RUNNING`, then per step `charge_or_exhaust()` →
`step.execute(ctx)` → persist, then `VALIDATING → COMPLETED` once every
step has succeeded. Every transition is persisted via `store.save()`
immediately, not batched at the end, so a concurrent `GET /runs/{id}` sees
live progress rather than a stale `RECEIVED` Run that quietly finished
elsewhere.

A step returning `ok=False` ends the Run at `FAILED_TERMINAL` with the
step's own `detail` as the reason. `request_status` is honoured through
the real `transition()` function, which means it inherits the real
transition table's restrictions — a step may ask for `FAILED_RETRYABLE` or
an abort from `RUNNING` (both legal edges) but not yet `AWAITING_APPROVAL`
(only legal from `VALIDATING`), because every step in a `Workflow.steps()`
list currently runs under one flat `RUNNING` phase. This is a known
boundary, not an oversight: it is exactly enough for `knowledge_qa`, which
never requests a status, and Phase 3's approval-bearing email workflow will
need `execute_workflow` extended to run steps in two phases (work, then
validate) before a step can legally ask for approval.

### `knowledge_qa` (Phase 1): `workflows/knowledge_qa.py`

Re-expresses V1's `answer_question()` as a `task_type` a Run can declare,
rather than a special-cased endpoint. `orchestration/pipeline.py` is
unchanged and `POST /query` still calls it directly — `knowledge_qa`'s one
step calls the same function, so retrieval and generation behave
identically either way; only the surrounding Run/workflow scaffolding is
new.

Two things about it are worth flagging because they read as
inconsistent with the rest of this document at first glance:

- **It does not call `ctx.gateway`.** A "pure" reading of `WorkflowStep`
  would have every external touch go through a registered capability, but
  retrieval and generation are not registered capabilities —
  `capabilities/bootstrap.py`'s catalogue has no "search the knowledge
  base" or "call the chat model" entry, and adding one is real, unplanned
  work that belongs with Phase 2 ("Gateway live"), not this phase. ACL
  enforcement continues to happen exactly where it always has — inside the
  index adapters (ADR-0002) — never via the capability gateway.
  `ctx.gateway`/`ctx.ledger` are still threaded through per
  `WorkflowContext`'s shape, unused, so a future step that does need a
  capability slots in without a signature change.
- **The registry holds an unbound template; the API route calls `.bind()`
  to get a request-scoped instance.** `WorkflowRegistry.register()` takes
  one instance and `steps()` takes no arguments, but each request needs
  its own question text and its own model/index clients — a shared
  registry entry cannot hold per-request data, and a frozen one could not
  hold it at all. The registered instance answers `task_type`/`version`
  lookups and `check_sufficiency()` (which only needs the static
  `required_zones`/`required_scopes`); `.bind(params)` returns a fresh
  instance whose `steps()` actually runs. Calling `.steps()` on the
  unbound template raises `RuntimeError` rather than returning something
  broken silently.

`required_scopes` names `kb.documents.read` — a scope `config/
policies.yaml` already granted to `all-staff`/`engineering` before this
workflow existed, but that nothing checked. `guest` holds neither, so a
`guest` Run declaring `knowledge_qa` is refused by `check_sufficiency()`
before a single retrieval call runs — a capability-scope deny path new
with this workflow, additional to (not a replacement for) V1's per-chunk
ACL enforcement, which `guest` also fails.

**The answer is never persisted on the Run.** Consistent with "What a Run
does not store" above: the `KnowledgeQaStep` keeps the full
`PipelineResult` on itself (not on the Run), and `POST /runs`' HTTP
response echoes it once to the caller who asked. `GET /runs/{id}` returns
only the durable Run record — status, terminal reason, budget/spend, and
`evidence_refs` (the retrieved chunk_ids, a reference, not the chunk text)
— the same trust boundary V1's `/query` already draws around the answer
text, just applied to a durable record with a wider read audience instead
of a single response body.

## API (Phase 1): `POST /runs`, `GET /runs/{id}`

`api/main.py`, alongside `/query`/`/whoami`, not in place of them. Same
mock identity resolution (`X-User-Id`, falling back to
`settings.default_user`), same fail-closed shape.

`POST /runs` never bypasses `runs/intake.py`: the route builds an
`IntakeRequest` from the resolved principal/groups and the request's
`purpose`/`task_type`, and `IntakeRejected` (unknown purpose, or a
task_type the purpose does not cover) becomes an HTTP 403 — no Run is
created. A workflow-level refusal is different and deliberately *not* an
HTTP error: if intake succeeds but `check_sufficiency()` or a budget check
later refuses (see `guest`'s missing `kb.documents.read` scope, above), a
Run *was* created, so the route returns 201 with that Run's terminal
status and reason — the refusal is the Run's own record, not a response
code. An HTTP error is reserved for requests where no valid Run could be
constructed or dispatched at all: an unknown `purpose`, a `task_type` no
workflow is registered for (400), or missing task-specific input (422,
e.g. `knowledge_qa` without `input.question`).

`GET /runs/{id}` answers a cross-principal read with **404, never 403**.
A 403 would confirm the run_id exists and simply belongs to someone else —
itself information a caller with no legitimate access to that Run should
not get. "Not yours" and "never existed" must be indistinguishable from
the outside.

The keyword-index construction rule from `/query` (build
`SqliteKeywordIndex` inside the route handler's own function body, never
through `Depends()`) carries over unchanged to `POST /runs`, for the same
reason — see [api-gateway-identity.md](api-gateway-identity.md) and
[retrieval.md](retrieval.md).

## Tradeoffs

- **Immutable aggregate vs. mutable object.** Threading a returned Run
  through every call is genuinely more annoying. It is also what makes
  "authority is carried, never generated" a type error rather than a
  convention — a step cannot widen `approved_scopes` in place.
- **Hand-written vs. a workflow engine.** No timers, no distributed
  scheduling, and the first store is a dict. See
  [ADR-0011](../decisions/0011-durable-run-aggregate.md) — the aggregate
  holds no infrastructure of its own precisely so an engine could go
  underneath it later.
- **Templates vs. a planner.** A template cannot handle a request nobody
  wrote it for. See
  [ADR-0014](../decisions/0014-deterministic-workflow-templates.md),
  including the graduation criteria that would change this.
- **`POST /runs`' budget override is caller-supplied, not policy-bounded
  (Phase 1).** Any caller can set their own `max_steps`/`max_tokens`/
  `max_spend_micros`/deadline on a Run they create. It exists so budget
  exhaustion — a required deny path for this phase — is reachable through
  the real HTTP endpoint, not only through `runs/service.py`'s unit tests.
  A real deployment would want this bounded by policy (a ceiling per
  purpose or tenant, the same shape zones and scopes already have), not
  trusted from the request body; that is a known gap, not an oversight.
- **`Run.with_checkpoint` was removed in Phase 1, not built on.** Phase 0
  added it as a seam with no caller; `runs/service.py`'s `execute_workflow`
  does not need mid-workflow checkpointing yet (`knowledge_qa` runs to
  completion synchronously within one call), so the setter was dead code
  rather than a seam a real phase was using. The `checkpoint` field stays
  on `Run` — it is still named in the schema this document describes —
  but the accessor comes back when a phase actually resumes a Run from
  one.
