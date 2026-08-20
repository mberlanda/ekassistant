"""Run state machine: the only legal way a Run changes status.

See docs/design/runs.md#state-machine.

The transition table is written out per-state rather than derived from a
rule, because the interesting content is the *exceptions* - which states
cannot be cancelled, which can loop back - and a derived table hides
exactly those.
"""

from ekassistant.runs.aggregate import Run, RunStatus

#: Ways a Run can stop without succeeding. Reachable from most working
#: states; COMMITTING_EFFECT is the deliberate exception, see below.
_ABORTS = frozenset(
    {
        RunStatus.REJECTED_POLICY,
        RunStatus.CANCELLED,
        RunStatus.BUDGET_EXHAUSTED,
        RunStatus.TIMED_OUT,
        RunStatus.FAILED_TERMINAL,
    }
)

#: No outgoing transitions. A terminal Run is a historical record; work
#: that continues after one is a new Run, so that the audit trail never
#: shows an outcome being revised after the fact.
TERMINAL_STATES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.REJECTED_POLICY,
        RunStatus.CANCELLED,
        RunStatus.BUDGET_EXHAUSTED,
        RunStatus.TIMED_OUT,
        RunStatus.FAILED_TERMINAL,
    }
)

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.RECEIVED: frozenset({RunStatus.CLARIFYING, RunStatus.PLANNED}) | _ABORTS,
    RunStatus.CLARIFYING: frozenset({RunStatus.PLANNED}) | _ABORTS,
    RunStatus.PLANNED: frozenset({RunStatus.RUNNING}) | _ABORTS,
    # RUNNING -> VALIDATING is the only forward edge: every workflow's
    # output is validated before it can be approved or returned. There is
    # no RUNNING -> COMPLETED shortcut, deliberately.
    RunStatus.RUNNING: frozenset({RunStatus.VALIDATING, RunStatus.FAILED_RETRYABLE}) | _ABORTS,
    # VALIDATING -> RUNNING is the bounded repair loop (a validator
    # rejected the draft, try again); the step budget is what stops it
    # from looping forever, not the state machine.
    # VALIDATING -> COMPLETED covers read-only runs, which have no effect
    # to approve or commit.
    RunStatus.VALIDATING: frozenset(
        {RunStatus.AWAITING_APPROVAL, RunStatus.COMPLETED, RunStatus.RUNNING}
    )
    | _ABORTS,
    # AWAITING_APPROVAL -> RUNNING is how an edit invalidates an approval:
    # the reviewer changed the payload, so the previously approved hash no
    # longer matches and the Run goes back to work rather than committing
    # something nobody approved. See capabilities/effects.py.
    RunStatus.AWAITING_APPROVAL: frozenset({RunStatus.COMMITTING_EFFECT, RunStatus.RUNNING})
    | _ABORTS,
    # COMMITTING_EFFECT deliberately cannot be CANCELLED or
    # BUDGET_EXHAUSTED. Once an irreversible external effect is in flight,
    # the outcome is uncertain rather than absent: the only honest exits
    # are success, a retryable failure that must reconcile the provider's
    # outcome before re-attempting, a terminal failure, or a timeout that
    # still leaves reconciliation owed. "Cancelled" would assert the
    # effect did not happen, which nothing here knows.
    RunStatus.COMMITTING_EFFECT: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.FAILED_RETRYABLE,
            RunStatus.FAILED_TERMINAL,
            RunStatus.TIMED_OUT,
        }
    ),
    # FAILED_RETRYABLE is a halt, not a terminal state - it is the resume
    # point. It diverges from docs/design/runs.md listing it among the
    # "terminal alternatives": treating it as truly terminal would mean a
    # transient provider error could only ever be recovered by creating a
    # new Run, losing the checkpoint and the effect ledger that make
    # recovery safe. Documented divergence, not an oversight.
    RunStatus.FAILED_RETRYABLE: frozenset({RunStatus.RUNNING, RunStatus.COMMITTING_EFFECT})
    | _ABORTS,
    RunStatus.COMPLETED: frozenset(),
    RunStatus.REJECTED_POLICY: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.BUDGET_EXHAUSTED: frozenset(),
    RunStatus.TIMED_OUT: frozenset(),
    RunStatus.FAILED_TERMINAL: frozenset(),
}


class InvalidTransition(Exception):
    """Raised on an illegal status change. An exception rather than a
    boolean return: an illegal transition is a bug in workflow code, not a
    condition a caller should branch on.
    """

    def __init__(self, current: RunStatus, requested: RunStatus):
        super().__init__(f"cannot transition Run from {current} to {requested}")
        self.current = current
        self.requested = requested


class MissingTerminalReason(Exception):
    """Raised when a Run is stopped without saying why.

    Enforced rather than defaulted to "unknown", because terminal_reason
    is what operational measurement is built on: a corpus of Runs that
    ended for "unknown" reasons cannot distinguish a policy rejection from
    a provider outage, which is precisely the distinction on-call needs.
    """


def can_transition(current: RunStatus, requested: RunStatus) -> bool:
    return requested in ALLOWED_TRANSITIONS[current]


def transition(run: Run, to: RunStatus, *, reason: str | None = None) -> Run:
    """Move a Run to `to`, or raise.

    `reason` is required for every terminal state except COMPLETED, and
    is also recorded for non-terminal transitions when supplied (useful
    for the FAILED_RETRYABLE halt, which carries the error that caused
    it).
    """
    if not can_transition(run.status, to):
        raise InvalidTransition(run.status, to)

    needs_reason = to in TERMINAL_STATES and to is not RunStatus.COMPLETED
    if needs_reason and not reason:
        raise MissingTerminalReason(f"transition to {to} requires a reason")

    return run._touch(status=to, terminal_reason=reason if reason else run.terminal_reason)


def is_terminal(run: Run) -> bool:
    return run.status in TERMINAL_STATES
