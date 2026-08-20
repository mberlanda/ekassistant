# Low-level design: Run Orchestration

Reference spine item c, at V2 scope. Where
[orchestration.md](orchestration.md) describes V1's single-shot question
pipeline, this describes the durable execution object that V2 workflows run
inside. Both exist: V1's pipeline keeps working and becomes one task
template among several in Phase 1.

See [ADR-0011](../decisions/0011-durable-run-aggregate.md) for why a Run
exists at all, and [ADR-0014](../decisions/0014-deterministic-workflow-templates.md)
for why workflows are templates rather than a planner loop.

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

**Owed:** a durable SQLite implementation, Phase 1.

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
