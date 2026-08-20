"""Run state machine: legal edges, and the deliberate exceptions."""

import pytest

from ekassistant.runs.aggregate import Run, RunStatus
from ekassistant.runs.machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransition,
    MissingTerminalReason,
    can_transition,
    is_terminal,
    transition,
)


def make_run(status: RunStatus = RunStatus.RECEIVED) -> Run:
    return Run(
        tenant_id="acme",
        principal_id="bob",
        purpose="client_communication",
        task_type="email_draft",
        status=status,
    )


def test_every_status_has_a_transition_entry():
    # A status added to the enum without a row here would raise KeyError
    # deep inside can_transition() at runtime instead of failing a test.
    assert set(ALLOWED_TRANSITIONS) == set(RunStatus)


def test_full_happy_path_including_an_effect():
    run = make_run()
    for status in (
        RunStatus.PLANNED,
        RunStatus.RUNNING,
        RunStatus.VALIDATING,
        RunStatus.AWAITING_APPROVAL,
        RunStatus.COMMITTING_EFFECT,
        RunStatus.COMPLETED,
    ):
        run = transition(run, status)

    assert run.status is RunStatus.COMPLETED
    assert is_terminal(run)


def test_read_only_run_completes_without_an_approval_or_commit():
    run = make_run()
    for status in (RunStatus.PLANNED, RunStatus.RUNNING, RunStatus.VALIDATING):
        run = transition(run, status)
    assert transition(run, RunStatus.COMPLETED).status is RunStatus.COMPLETED


def test_there_is_no_running_to_completed_shortcut():
    # Output always passes through validation. Pinned because a
    # "fast path" here would skip every egress check.
    assert not can_transition(RunStatus.RUNNING, RunStatus.COMPLETED)


def test_illegal_transition_raises():
    run = make_run()
    with pytest.raises(InvalidTransition) as exc:
        transition(run, RunStatus.COMMITTING_EFFECT)

    assert exc.value.current is RunStatus.RECEIVED
    assert exc.value.requested is RunStatus.COMMITTING_EFFECT


def test_terminal_states_have_no_exits():
    for status in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[status] == frozenset(), status


def test_terminal_transitions_require_a_reason_except_completed():
    run = transition(transition(make_run(), RunStatus.PLANNED), RunStatus.RUNNING)

    with pytest.raises(MissingTerminalReason):
        transition(run, RunStatus.FAILED_TERMINAL)

    stopped = transition(run, RunStatus.FAILED_TERMINAL, reason="model gateway unavailable")
    assert stopped.terminal_reason == "model gateway unavailable"


def test_completed_needs_no_reason():
    run = make_run(RunStatus.VALIDATING)
    assert transition(run, RunStatus.COMPLETED).terminal_reason is None


def test_committing_effect_cannot_be_cancelled_or_budget_exhausted():
    # Once an irreversible effect is in flight the outcome is unknown, not
    # absent. CANCELLED would assert it did not happen.
    assert not can_transition(RunStatus.COMMITTING_EFFECT, RunStatus.CANCELLED)
    assert not can_transition(RunStatus.COMMITTING_EFFECT, RunStatus.BUDGET_EXHAUSTED)
    assert can_transition(RunStatus.COMMITTING_EFFECT, RunStatus.FAILED_RETRYABLE)
    assert can_transition(RunStatus.COMMITTING_EFFECT, RunStatus.TIMED_OUT)


def test_editing_an_approved_draft_sends_the_run_back_to_work():
    # AWAITING_APPROVAL -> RUNNING is how a payload edit invalidates an
    # approval without inventing a state for it.
    assert can_transition(RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)


def test_failed_retryable_is_a_resume_point_not_an_ending():
    assert RunStatus.FAILED_RETRYABLE not in TERMINAL_STATES
    assert can_transition(RunStatus.FAILED_RETRYABLE, RunStatus.RUNNING)
    assert can_transition(RunStatus.FAILED_RETRYABLE, RunStatus.COMMITTING_EFFECT)


def test_validation_can_loop_back_for_repair():
    assert can_transition(RunStatus.VALIDATING, RunStatus.RUNNING)


def test_transition_preserves_an_earlier_reason_when_none_is_given():
    run = transition(make_run(), RunStatus.PLANNED, reason="intake note")
    assert transition(run, RunStatus.RUNNING).terminal_reason == "intake note"
