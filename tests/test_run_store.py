"""InMemoryRunStore: the mock enforces what the durable store will."""

import pytest

from ekassistant.runs.aggregate import Run
from ekassistant.runs.store import ConcurrentRunUpdate, InMemoryRunStore, RunNotFound


def make_run(principal_id: str = "bob", tenant_id: str = "acme") -> Run:
    return Run(
        tenant_id=tenant_id,
        principal_id=principal_id,
        purpose="client_communication",
        task_type="email_draft",
    )


def test_create_then_get_round_trips():
    store = InMemoryRunStore()
    run = store.create(make_run())
    assert store.get(run.run_id) == run


def test_get_unknown_raises():
    with pytest.raises(RunNotFound):
        InMemoryRunStore().get("run_missing")


def test_create_twice_raises():
    store = InMemoryRunStore()
    run = store.create(make_run())
    with pytest.raises(ConcurrentRunUpdate):
        store.create(run)


def test_save_requires_an_existing_run():
    with pytest.raises(RunNotFound):
        InMemoryRunStore().save(make_run())


def test_save_rejects_a_stale_copy():
    # The lost-update case that matters: an approval callback writes, then
    # an in-flight step saves the copy it read beforehand. Silently
    # accepting the second write would discard the approval.
    store = InMemoryRunStore()
    run = store.create(make_run())

    fresh = store.save(run.charge(steps=1))
    stale = run.charge(steps=1)
    object.__setattr__(stale, "updated_at", fresh.updated_at - 1)

    with pytest.raises(ConcurrentRunUpdate):
        store.save(stale)


def test_list_for_principal_is_scoped_by_tenant_and_principal():
    store = InMemoryRunStore()
    mine = store.create(make_run("bob", "acme"))
    store.create(make_run("carol", "acme"))
    store.create(make_run("bob", "other-tenant"))

    found = store.list_for_principal("acme", "bob")
    assert [r.run_id for r in found] == [mine.run_id]


def test_wrong_tenant_returns_nothing_rather_than_everything():
    store = InMemoryRunStore()
    store.create(make_run("bob", "acme"))
    assert store.list_for_principal("", "bob") == []
