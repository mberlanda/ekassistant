"""SqliteRunStore: the durable RunStore, held to the same contract as
InMemoryRunStore (test_run_store.py) - it must reject everything the mock
rejects, plus survive genuine cross-thread/cross-connection concurrency,
which the mock never has to.
"""

import threading
import time

import pytest

from ekassistant.capabilities.contracts import Classification, Zone
from ekassistant.runs.aggregate import Budget, BudgetSpend, RiskTier, Run, StepResult
from ekassistant.runs.sqlite_store import SqliteRunStore
from ekassistant.runs.store import ConcurrentRunUpdate, RunNotFound


def make_run(principal_id: str = "bob", tenant_id: str = "acme", **kwargs) -> Run:
    defaults = dict(
        tenant_id=tenant_id,
        principal_id=principal_id,
        purpose="client_communication",
        task_type="email_draft",
    )
    return Run(**{**defaults, **kwargs})


@pytest.fixture
def store(tmp_path) -> SqliteRunStore:
    return SqliteRunStore(tmp_path / "runs.sqlite3")


def test_create_then_get_round_trips(store):
    run = store.create(make_run())
    assert store.get(run.run_id) == run


def test_round_trip_preserves_every_field_group(store):
    # Not just the identity fields - a lossy serializer would pass the
    # simple round-trip test above while quietly dropping something like
    # step_results or a frozenset ordering.
    run = make_run(
        approved_scopes=frozenset({"email.send", "crm.accounts.read"}),
        allowed_zones=frozenset({Zone.INTERNAL_DATA, Zone.EFFECT}),
        classification_ceiling=Classification.RESTRICTED,
        risk_tier=RiskTier.HIGH,
        step_results=(
            StepResult(
                step_name="retrieve",
                contract_ref="crm_account_lookup@1",
                ok=True,
                evidence_ref="ev-1",
                detail="found 3",
                started_at=1.0,
                duration_ms=12.5,
            ),
        ),
        input_refs=("inline:abc",),
        evidence_refs=("ev-1", "ev-2"),
        effect_intent_ids=("eff-1",),
        budget=Budget(max_steps=7, max_tokens=999, max_spend_micros=5, deadline_ts=123.0),
        spend=BudgetSpend(steps=2, tokens=10, spend_micros=1),
        terminal_reason=None,
        checkpoint="chk-1",
    )
    store.create(run)

    round_tripped = store.get(run.run_id)
    assert round_tripped == run


def test_get_unknown_raises(store):
    with pytest.raises(RunNotFound):
        store.get("run_missing")


def test_create_twice_raises(store):
    run = store.create(make_run())
    with pytest.raises(ConcurrentRunUpdate):
        store.create(run)


def test_save_requires_an_existing_run(store):
    with pytest.raises(RunNotFound):
        store.save(make_run())


def test_save_rejects_a_stale_copy(store):
    # Same lost-update scenario test_run_store.py pins for the mock: an
    # approval callback writes, then an in-flight step saves the copy it
    # read beforehand. The durable store must refuse this too.
    run = store.create(make_run())

    fresh = store.save(run.charge(steps=1))
    stale = run.charge(steps=1)
    object.__setattr__(stale, "updated_at", fresh.updated_at - 1)

    with pytest.raises(ConcurrentRunUpdate):
        store.save(stale)

    # And the stale write must not have landed.
    assert store.get(run.run_id) == fresh


def test_save_accepts_a_forward_write(store):
    run = store.create(make_run())
    charged = store.save(run.charge(steps=2, tokens=50))
    assert store.get(run.run_id).spend == BudgetSpend(steps=2, tokens=50)
    assert charged.spend == BudgetSpend(steps=2, tokens=50)


def test_list_for_principal_is_scoped_by_tenant_and_principal(store):
    mine = store.create(make_run("bob", "acme"))
    store.create(make_run("carol", "acme"))
    store.create(make_run("bob", "other-tenant"))

    found = store.list_for_principal("acme", "bob")
    assert [r.run_id for r in found] == [mine.run_id]


def test_wrong_tenant_returns_nothing_rather_than_everything(store):
    store.create(make_run("bob", "acme"))
    assert store.list_for_principal("", "bob") == []


def test_list_for_principal_is_ordered_by_creation(store):
    first = store.create(make_run("bob", "acme"))
    time.sleep(0.001)
    second = store.create(make_run("bob", "acme"))

    found = store.list_for_principal("acme", "bob")
    assert [r.run_id for r in found] == [first.run_id, second.run_id]


def test_a_second_store_instance_over_the_same_path_sees_the_same_data(tmp_path):
    # Proves durability across the store object's own lifetime, not just
    # within one Python object - a real restart re-opens the same file.
    path = tmp_path / "runs.sqlite3"
    run = SqliteRunStore(path).create(make_run())

    reopened = SqliteRunStore(path)
    assert reopened.get(run.run_id) == run


def test_concurrent_creates_from_real_threads_all_succeed(store):
    # Each thread constructs and uses its own connection inside the same
    # method call (see sqlite_store.py's module docstring) - this proves
    # that in practice, not just by argument, by firing genuinely
    # concurrent creates from real OS threads (not a sequential loop) and
    # checking none raised sqlite3.ProgrammingError or any other
    # cross-thread failure.
    errors: list[BaseException] = []
    created_ids: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            run = store.create(make_run(principal_id=f"user-{i}"))
            with lock:
                created_ids.append(run.run_id)
        except BaseException as exc:  # noqa: BLE001 - want to see everything
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(created_ids) == 20
    assert len(set(created_ids)) == 20


def test_concurrent_saves_never_cross_thread_error_or_corrupt(store):
    # Real OS threads (not a sequential loop) hammering save() on the
    # same run must never raise a cross-thread sqlite error, and every
    # commit must leave the store holding a value one of the threads
    # actually wrote, never a torn/corrupted write from two commits
    # interleaving.
    #
    # This deliberately does NOT assert that every thread's charge
    # survives. The concurrency check here is a wall-clock comparison
    # (`stored.updated_at <= incoming.updated_at`), matching
    # InMemoryRunStore exactly (see test_save_rejects_a_stale_copy, which
    # only demonstrates a rejection by artificially setting a timestamp
    # backwards) - not a true read-token/version check. Two threads that
    # each independently read-then-charge from the same stale snapshot
    # can both legitimately pass that comparison, because `charge()`
    # always stamps the current wall-clock time and time only moves
    # forward, so a later write is (correctly, by this store's actual
    # contract) never treated as "older than what's stored" just because
    # its author read a stale copy. That is an inherited property of the
    # mock's contract, not a regression introduced by the durable store,
    # so it is not something this test can honestly assert against.
    run = store.create(make_run())
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            current = store.get(run.run_id)
            store.save(current.charge(steps=1, tokens=i))
        except ConcurrentRunUpdate:
            pass
        except BaseException as exc:  # noqa: BLE001 - want to see everything
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = store.get(run.run_id)
    # Not a fixed number: a worker's own re-read can legitimately observe
    # another worker's already-committed charge and build on it (a
    # correct chain, not corruption), so the final count is anywhere from
    # "only one write ever landed" to "every write chained off the last".
    # What must never happen is a value outside that range, which is what
    # a torn or duplicated write would produce.
    assert 1 <= final.spend.steps <= len(threads)
