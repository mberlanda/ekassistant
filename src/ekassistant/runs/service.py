"""The Run service: drives a Run through a workflow template, persisting
as it goes, and is where budget enforcement is actually wired to the
state machine.

See docs/design/runs.md#budgets and #workflows, and ADR-0011/ADR-0014.

Phase 0 shipped the pieces this module connects, but did not connect
them: `Run.would_exhaust()` *reports* which budget dimension a charge
would break, `runs/machine.py` *knows how* to terminate a Run with a
reason, and neither called the other. `charge_or_exhaust()` is that
connection - the one place a workflow step's budget charge either
succeeds or the Run stops, instead of a caller having to remember to
check `would_exhaust()` before every `charge()`.

`execute_workflow()` is the generic driver both `check_sufficiency` and
the budget wiring serve: given a Run already fixed by intake and a
`Workflow` template, it is the whole RECEIVED -> ... -> COMPLETED path,
persisting via `RunStore` after every transition so a concurrent
`GET /runs/{id}` sees live progress rather than a stale RECEIVED Run
that silently finished elsewhere.
"""

from ekassistant.capabilities.effects import EffectLedger
from ekassistant.capabilities.gateway import CapabilityGateway
from ekassistant.runs.aggregate import Run, RunStatus, StepResult
from ekassistant.runs.machine import is_terminal, transition
from ekassistant.runs.store import RunStore
from ekassistant.workflows.base import Workflow, WorkflowContext, check_sufficiency

#: `Run.would_exhaust()`'s dimension names that map to TIMED_OUT rather
#: than the default BUDGET_EXHAUSTED. Only "deadline" does: a Run that
#: ran out of wall-clock time did not necessarily run out of steps,
#: tokens or spend, and docs/design/runs.md keeps those as separate
#: terminal reasons on purpose - an operator asking "did this stall, or
#: did it just do a lot of work" needs the distinction.
_TIMEOUT_DIMENSIONS = frozenset({"deadline"})


def _terminal_status_for(dimension: str) -> RunStatus:
    return RunStatus.TIMED_OUT if dimension in _TIMEOUT_DIMENSIONS else RunStatus.BUDGET_EXHAUSTED


def charge_or_exhaust(
    run: Run, *, steps: int = 1, tokens: int = 0, spend_micros: int = 0, now: float | None = None
) -> Run:
    """Charge a Run for one unit of work, or stop it if that would break
    its budget.

    This is the missing wire from docs/design/runs.md's budget section:
    `would_exhaust()` checks, `charge()` records, and *this* function is
    what decides which of the two happens and, on exhaustion, drives the
    state machine to the correctly-named terminal state with a reason -
    "max_steps exhausted" and "deadline exhausted" must stay
    distinguishable in a corpus of terminated Runs, which is exactly what
    `transition()` already enforces by requiring a reason at all.

    Returns the charged Run on success, or a Run already transitioned to
    BUDGET_EXHAUSTED/TIMED_OUT on exhaustion - callers should check
    `runs.machine.is_terminal(run)` (or `run.status`) after calling this
    before doing any more work, the same way `gateway.invoke()`'s
    denied-but-charged return already asks callers to check `allowed`.
    """
    dimension = run.would_exhaust(steps=steps, tokens=tokens, spend_micros=spend_micros, now=now)
    if dimension is None:
        return run.charge(steps=steps, tokens=tokens, spend_micros=spend_micros)
    return transition(
        run,
        _terminal_status_for(dimension),
        reason=(
            f"{dimension} exhausted before charging steps={steps} tokens={tokens} "
            f"spend_micros={spend_micros}"
        ),
    )


def execute_workflow(
    run: Run,
    workflow: Workflow,
    store: RunStore,
    gateway: CapabilityGateway,
    ledger: EffectLedger,
    *,
    now: float | None = None,
) -> Run:
    """Drive `run` through `workflow` to completion or a halt, persisting
    every transition.

    Pre-flight sufficiency (docs/design/runs.md#workflows) runs before
    anything is charged or attempted: a Run that would be denied on its
    third step should never have started the first two. Every subsequent
    step is preceded by `charge_or_exhaust()`, so a workflow with more
    steps than the Run's `max_steps` budget allows stops cleanly between
    steps rather than partway through one, and any capability calls a
    step itself makes through `ctx.gateway` share the same `spend.steps`
    counter (gateway.invoke() charges it too) - so the *next* step's
    pre-check is what catches a step that exhausted the budget on tool
    calls, not a special case here.

    A step returning `ok=False` ends the Run at FAILED_TERMINAL with the
    step's own detail as the reason. Distinguishing "failed because a
    capability call inside the step was itself budget-denied" from a
    genuine logic failure is not attempted here - `charge_or_exhaust()`'s
    pre-step check is what actually stops a Run before it starves a
    budget across many steps; a single step's own internal budget denial
    is visible in the capability audit log's per-invocation record
    (docs/design/capabilities.md#audit), not reclassified here.

    Known limitation, left for whichever workflow first needs it (Phase
    3's client-email approval flow, most likely): every step in
    `workflow.steps()` runs while the Run's status is RUNNING, and
    `request_status` is honoured via `runs.machine.transition()`, which
    enforces the real transition table - so a step may request
    FAILED_RETRYABLE or an abort (both legal from RUNNING) but *not*
    AWAITING_APPROVAL, which is only reachable from VALIDATING. This
    driver does not yet insert a VALIDATING phase between "steps that do
    work" and "a step that asks for approval"; a workflow needing that
    shape will need this function extended to run steps in two phases,
    not just one more `request_status` value accepted.
    """
    sufficiency = check_sufficiency(run, workflow)
    if not sufficiency.ok:
        run = transition(run, RunStatus.REJECTED_POLICY, reason=sufficiency.reason)
        return store.save(run)

    run = store.save(transition(run, RunStatus.PLANNED))
    run = store.save(transition(run, RunStatus.RUNNING))

    for step in workflow.steps():
        run = charge_or_exhaust(run, steps=1, now=now)
        if is_terminal(run):
            return store.save(run)

        outcome = step.execute(WorkflowContext(run=run, gateway=gateway, ledger=ledger))
        run = outcome.run
        if outcome.evidence_refs:
            run = run.with_evidence(*outcome.evidence_refs)
        run = run.with_step(
            StepResult(
                step_name=step.spec.name,
                contract_ref=None,
                ok=outcome.ok,
                detail=outcome.detail,
            )
        )

        if not outcome.ok:
            reason = outcome.detail or f"step {step.spec.name!r} failed"
            return store.save(transition(run, RunStatus.FAILED_TERMINAL, reason=reason))

        if outcome.request_status is not None:
            run = transition(run, outcome.request_status, reason=outcome.terminal_reason)
            run = store.save(run)
            # Anything other than RUNNING is a stopping point for this
            # call: a terminal state needs no more work, and a pause like
            # AWAITING_APPROVAL means what comes next is a separate
            # entry point (an approval callback moving the Run back to
            # RUNNING), not more steps executed in the same pass.
            if run.status is not RunStatus.RUNNING:
                return run
        else:
            run = store.save(run)

    run = store.save(transition(run, RunStatus.VALIDATING))
    run = store.save(transition(run, RunStatus.COMPLETED))
    return run
