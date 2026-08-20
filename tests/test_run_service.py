"""runs/service.py: budget enforcement actually wired to the state
machine (charge_or_exhaust), and the generic workflow-execution driver
every task template runs through (execute_workflow).

Phase 0 shipped would_exhaust() (reports) and the state machine's
terminal-state-with-reason vocabulary (enforces), but nothing called one
from the other - that connection is what this file tests.
"""

import time

import pytest

from ekassistant.capabilities.contracts import Zone
from ekassistant.runs.aggregate import Budget, RunStatus
from ekassistant.runs.machine import InvalidTransition, transition
from ekassistant.runs.service import charge_or_exhaust, execute_workflow
from ekassistant.runs.store import InMemoryRunStore
from ekassistant.workflows.base import StepOutcome, StepSpec

# -- charge_or_exhaust -------------------------------------------------


def _running(run):
    return transition(transition(run, RunStatus.PLANNED), RunStatus.RUNNING)


def test_charges_when_within_budget(make_run):
    run = make_run(budget=Budget(max_steps=5))
    charged = charge_or_exhaust(run, steps=1, tokens=10)

    assert charged.spend.steps == 1
    assert charged.spend.tokens == 10
    assert charged.status is run.status  # unchanged, still RECEIVED


def test_stops_on_max_steps_with_a_named_reason(make_run):
    run = _running(make_run(budget=Budget(max_steps=1)))
    run = charge_or_exhaust(run, steps=1)  # exactly at the limit, allowed

    stopped = charge_or_exhaust(run, steps=1)

    assert stopped.status is RunStatus.BUDGET_EXHAUSTED
    assert "max_steps" in stopped.terminal_reason


def test_stops_on_max_tokens(make_run):
    run = _running(make_run(budget=Budget(max_tokens=10)))
    stopped = charge_or_exhaust(run, tokens=11)

    assert stopped.status is RunStatus.BUDGET_EXHAUSTED
    assert "max_tokens" in stopped.terminal_reason


def test_stops_on_max_spend(make_run):
    run = _running(make_run(budget=Budget(max_spend_micros=0)))
    stopped = charge_or_exhaust(run, spend_micros=1)

    assert stopped.status is RunStatus.BUDGET_EXHAUSTED
    assert "max_spend_micros" in stopped.terminal_reason


def test_a_passed_deadline_maps_to_timed_out_not_budget_exhausted(make_run):
    # would_exhaust() reports "deadline" as just another dimension name.
    # The mapping to TIMED_OUT (rather than the default BUDGET_EXHAUSTED)
    # is the whole point of this function existing rather than a one-line
    # `if would_exhaust(): transition(..., BUDGET_EXHAUSTED)` - a corpus
    # of terminated Runs needs "stalled" distinguishable from "did a lot
    # of work" (docs/design/runs.md).
    run = _running(make_run(budget=Budget(deadline_ts=time.time() - 1)))
    stopped = charge_or_exhaust(run)

    assert stopped.status is RunStatus.TIMED_OUT
    assert "deadline" in stopped.terminal_reason


def test_never_returns_a_run_missing_a_terminal_reason(make_run):
    run = _running(make_run(budget=Budget(max_steps=0)))
    stopped = charge_or_exhaust(run, steps=1)
    assert stopped.terminal_reason


def test_defers_to_the_state_machines_own_exceptions_rather_than_inventing_a_bypass(make_run):
    # machine.py's rule: COMMITTING_EFFECT cannot go to BUDGET_EXHAUSTED
    # (an in-flight irreversible effect's outcome is unknown, not
    # absent). charge_or_exhaust must not route around that with its own
    # logic - a caller misusing it here should get the same
    # InvalidTransition anything else calling transition() badly would.
    run = make_run(budget=Budget(max_steps=0))
    for status in (
        RunStatus.PLANNED,
        RunStatus.RUNNING,
        RunStatus.VALIDATING,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.COMMITTING_EFFECT,
    ):
        run = transition(run, status)

    with pytest.raises(InvalidTransition):
        charge_or_exhaust(run, steps=1)


# -- execute_workflow ---------------------------------------------------


class RecordingStep:
    def __init__(
        self,
        name,
        *,
        ok=True,
        detail=None,
        evidence_refs=(),
        request_status=None,
        terminal_reason=None,
    ):
        self.spec = StepSpec(name=name)
        self._ok = ok
        self._detail = detail
        self._evidence_refs = evidence_refs
        self._request_status = request_status
        self._terminal_reason = terminal_reason
        self.executed = False

    def execute(self, ctx) -> StepOutcome:
        self.executed = True
        return StepOutcome(
            run=ctx.run,
            ok=self._ok,
            detail=self._detail,
            evidence_refs=self._evidence_refs,
            request_status=self._request_status,
            terminal_reason=self._terminal_reason,
        )


class FakeWorkflow:
    task_type = "email_draft"
    version = 1

    def __init__(
        self,
        steps,
        *,
        required_zones=frozenset({Zone.EFFECT}),
        required_scopes=frozenset({"email.drafts.write"}),
    ):
        self._steps = steps
        self.required_zones = required_zones
        self.required_scopes = required_scopes

    def steps(self):
        return self._steps


def test_runs_every_step_to_completion_and_persists_along_the_way(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run())
    step = RecordingStep("draft", evidence_refs=("ev-1", "ev-2"))
    workflow = FakeWorkflow([step])

    final = execute_workflow(run, workflow, store, gateway, ledger)

    assert final.status is RunStatus.COMPLETED
    assert step.executed
    assert final.evidence_refs == ("ev-1", "ev-2")
    assert [s.step_name for s in final.step_results] == ["draft"]
    # Persisted, not just returned - a concurrent GET must see this too.
    assert store.get(final.run_id).status is RunStatus.COMPLETED


def test_rejects_before_running_anything_when_the_run_lacks_authority(make_run, gateway, ledger):
    store = InMemoryRunStore()
    # carol (finance, all-staff) never holds email.send.
    run = store.create(make_run("carol"))
    step = RecordingStep("send")
    workflow = FakeWorkflow(
        [step], required_zones=frozenset({Zone.EFFECT}), required_scopes=frozenset({"email.send"})
    )

    final = execute_workflow(run, workflow, store, gateway, ledger)

    assert final.status is RunStatus.REJECTED_POLICY
    assert not step.executed
    assert "email.send" in final.terminal_reason


def test_stops_at_budget_exhausted_between_steps_not_mid_step(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run(budget=Budget(max_steps=1)))
    step1 = RecordingStep("first")
    step2 = RecordingStep("second")
    workflow = FakeWorkflow([step1, step2])

    final = execute_workflow(run, workflow, store, gateway, ledger)

    assert final.status is RunStatus.BUDGET_EXHAUSTED
    assert "max_steps" in final.terminal_reason
    assert step1.executed
    assert not step2.executed
    assert store.get(final.run_id).status is RunStatus.BUDGET_EXHAUSTED


def test_an_already_passed_deadline_stops_before_the_first_step(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run(budget=Budget(deadline_ts=time.time() - 1)))
    step = RecordingStep("draft")
    workflow = FakeWorkflow([step])

    final = execute_workflow(run, workflow, store, gateway, ledger)

    assert final.status is RunStatus.TIMED_OUT
    assert not step.executed


def test_fails_terminal_when_a_step_reports_not_ok(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run())
    step = RecordingStep("draft", ok=False, detail="missing recipient")
    workflow = FakeWorkflow([step])

    final = execute_workflow(run, workflow, store, gateway, ledger)

    assert final.status is RunStatus.FAILED_TERMINAL
    assert final.terminal_reason == "missing recipient"


def test_a_requested_pause_stops_the_run_before_later_steps(make_run, gateway, ledger):
    # FAILED_RETRYABLE, not AWAITING_APPROVAL: the latter is only legal
    # from VALIDATING (see execute_workflow's docstring for why this
    # driver does not yet support that request from inside the RUNNING
    # loop). FAILED_RETRYABLE is legal directly from RUNNING and is
    # itself a "halt, not an ending" - the right shape for this test.
    store = InMemoryRunStore()
    run = store.create(make_run())
    step1 = RecordingStep(
        "draft",
        request_status=RunStatus.FAILED_RETRYABLE,
        terminal_reason="provider timeout, safe to retry",
    )
    step2 = RecordingStep("send")
    workflow = FakeWorkflow([step1, step2])

    final = execute_workflow(run, workflow, store, gateway, ledger)

    assert final.status is RunStatus.FAILED_RETRYABLE
    assert final.terminal_reason == "provider timeout, safe to retry"
    assert step1.executed
    assert not step2.executed
