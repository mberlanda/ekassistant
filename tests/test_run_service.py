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
from ekassistant.runs.service import (
    StepExecutionFailed,
    charge_or_exhaust,
    execute_workflow,
)
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


# -- a step that raises rather than returning an outcome ----------------
#
# Regression: before this was handled, an exception out of step.execute()
# left the Run durably at RUNNING with terminal_reason=None and the step's
# budget charge discarded, because every store.save() in the loop happens
# after execute() returns. A later GET could not tell that Run apart from
# one still legitimately in progress.


class ExplodingStep:
    def __init__(self, name="boom", exc=None):
        self.spec = StepSpec(name=name)
        self._exc = exc or ConnectionError("provider unreachable at https://host/x?token=sekrit")

    def execute(self, ctx):
        raise self._exc


def test_a_raising_step_halts_the_run_at_failed_retryable(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run())
    workflow = FakeWorkflow([ExplodingStep()])

    with pytest.raises(StepExecutionFailed) as caught:
        execute_workflow(run, workflow, store, gateway, ledger)

    assert caught.value.run.status is RunStatus.FAILED_RETRYABLE
    assert caught.value.step_name == "boom"


def test_the_halted_run_is_persisted_not_just_returned(make_run, gateway, ledger):
    # The whole point: a caller who never sees the exception must still be
    # able to read the true status back out of the store.
    store = InMemoryRunStore()
    run = store.create(make_run())

    with pytest.raises(StepExecutionFailed):
        execute_workflow(run, FakeWorkflow([ExplodingStep()]), store, gateway, ledger)

    assert store.get(run.run_id).status is RunStatus.FAILED_RETRYABLE


def test_the_halt_carries_a_reason_naming_the_exception_type(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run())

    with pytest.raises(StepExecutionFailed):
        execute_workflow(run, FakeWorkflow([ExplodingStep()]), store, gateway, ledger)

    reason = store.get(run.run_id).terminal_reason
    assert "ConnectionError" in reason
    assert "boom" in reason


def test_the_exception_message_is_never_copied_into_the_run_record(make_run, gateway, ledger):
    # terminal_reason and step detail are exposed through RunResponse, and
    # exception messages are not curated for that audience - this one
    # carries a URL and a token. The type is recorded; the message is not.
    store = InMemoryRunStore()
    run = store.create(make_run())

    with pytest.raises(StepExecutionFailed):
        execute_workflow(run, FakeWorkflow([ExplodingStep()]), store, gateway, ledger)

    halted = store.get(run.run_id)
    recorded = f"{halted.terminal_reason} {halted.step_results[-1].detail}"
    assert "sekrit" not in recorded
    assert "https://host" not in recorded
    assert "ConnectionError" in recorded


def test_the_charge_for_the_attempted_step_survives(make_run, gateway, ledger):
    # Otherwise a step that reliably explodes is free, and a retry loop
    # around it never exhausts a budget.
    store = InMemoryRunStore()
    run = store.create(make_run())

    with pytest.raises(StepExecutionFailed):
        execute_workflow(run, FakeWorkflow([ExplodingStep()]), store, gateway, ledger)

    assert store.get(run.run_id).spend.steps == 1


def test_the_failed_step_is_recorded_as_a_step_result(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run())

    with pytest.raises(StepExecutionFailed):
        execute_workflow(run, FakeWorkflow([ExplodingStep()]), store, gateway, ledger)

    result = store.get(run.run_id).step_results[-1]
    assert result.step_name == "boom"
    assert result.ok is False


def test_the_original_exception_is_chained_not_swallowed(make_run, gateway, ledger):
    # An infrastructure failure must stay diagnosable in the server log.
    store = InMemoryRunStore()
    run = store.create(make_run())
    original = TimeoutError("upstream timed out")

    with pytest.raises(StepExecutionFailed) as caught:
        execute_workflow(
            run, FakeWorkflow([ExplodingStep(exc=original)]), store, gateway, ledger
        )

    assert caught.value.__cause__ is original
    assert caught.value.cause is original


def test_a_later_step_never_runs_after_one_raises(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run())
    later = RecordingStep("later")

    with pytest.raises(StepExecutionFailed):
        execute_workflow(
            run, FakeWorkflow([ExplodingStep(), later]), store, gateway, ledger
        )

    assert later.executed is False


# -- step timing --------------------------------------------------------


def test_a_successful_step_records_real_timing(make_run, gateway, ledger):
    # StepResult.started_at/duration_ms were left at their 0.0 defaults by
    # the only driver that produces them, which made "operational
    # measurement" in runs.md a claim with nothing behind it.
    store = InMemoryRunStore()
    run = store.create(make_run())

    final = execute_workflow(run, FakeWorkflow([RecordingStep("draft")]), store, gateway, ledger)

    result = final.step_results[-1]
    assert result.started_at > 0.0
    assert result.duration_ms >= 0.0


def test_a_raising_step_still_records_timing(make_run, gateway, ledger):
    store = InMemoryRunStore()
    run = store.create(make_run())

    with pytest.raises(StepExecutionFailed):
        execute_workflow(run, FakeWorkflow([ExplodingStep()]), store, gateway, ledger)

    assert store.get(run.run_id).step_results[-1].started_at > 0.0
