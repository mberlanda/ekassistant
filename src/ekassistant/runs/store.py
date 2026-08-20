"""Run persistence: the Protocol, plus the in-memory mock.

See docs/design/runs.md#persistence. The durable SQLite implementation
lands with the Run service (Phase 1); this module ships the seam and a
working mock so workflow and gateway code can be written and tested
against the real interface first.

The Protocol is intentionally small - create/get/save/list - because a
Run is a single aggregate written as a whole snapshot. Field-level update
methods would let two concurrent writers each persist a partial view and
lose the other's change; `save` taking the whole Run makes the
optimistic-concurrency check below possible instead.
"""

from typing import Protocol

from ekassistant.runs.aggregate import Run


class RunNotFound(KeyError):
    pass


class ConcurrentRunUpdate(Exception):
    """Raised when a save would overwrite a newer version of the Run.

    Runs are long-lived and touched from more than one place (a workflow
    step, an approval callback, a cancellation), so lost updates are a
    real failure mode rather than a theoretical one - an approval silently
    overwritten by an in-flight step is how something unapproved gets
    committed.
    """


class RunStore(Protocol):
    def create(self, run: Run) -> Run: ...

    def get(self, run_id: str) -> Run: ...

    def save(self, run: Run) -> Run: ...

    def list_for_principal(self, tenant_id: str, principal_id: str) -> list[Run]: ...


class InMemoryRunStore:
    """Mock RunStore. Not durable, not thread-safe across processes -
    suitable for tests and for the Phase 0 skeleton only.

    It does implement the optimistic-concurrency check, though, because a
    mock that accepts writes the real store would reject teaches callers
    the wrong contract.
    """

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def create(self, run: Run) -> Run:
        if run.run_id in self._runs:
            raise ConcurrentRunUpdate(f"run {run.run_id} already exists")
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFound(run_id) from exc

    def save(self, run: Run) -> Run:
        stored = self._runs.get(run.run_id)
        if stored is None:
            raise RunNotFound(run.run_id)
        if stored.updated_at > run.updated_at:
            raise ConcurrentRunUpdate(
                f"run {run.run_id} was modified at {stored.updated_at}, "
                f"later than this copy's {run.updated_at}"
            )
        self._runs[run.run_id] = run
        return run

    def list_for_principal(self, tenant_id: str, principal_id: str) -> list[Run]:
        # Tenant is part of the key, not a post-filter: a caller that
        # forgets to pass it gets nothing rather than everything.
        return [
            run
            for run in self._runs.values()
            if run.tenant_id == tenant_id and run.principal_id == principal_id
        ]
