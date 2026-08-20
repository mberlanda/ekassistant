"""Run aggregate: authority, budget accounting and immutability."""

import dataclasses
import time

import pytest

from ekassistant.capabilities.contracts import Classification, Zone
from ekassistant.runs.aggregate import Budget, BudgetSpend, RiskTier, Run, StepResult


def make_run(**kwargs) -> Run:
    defaults = dict(
        tenant_id="acme",
        principal_id="bob",
        purpose="client_communication",
        task_type="email_draft",
    )
    return Run(**{**defaults, **kwargs})


def test_defaults_carry_no_authority():
    # The zero-authority default is the whole point: a Run built without
    # going through intake must be able to do nothing at all.
    run = make_run()
    assert run.approved_scopes == frozenset()
    assert run.allowed_zones == frozenset()
    assert run.classification_ceiling is Classification.PUBLIC
    assert not run.permits_zone(Zone.INTERNAL_DATA)


def test_grants_requires_every_scope():
    run = make_run(approved_scopes=frozenset({"a", "b"}))
    assert run.grants(frozenset({"a"}))
    assert run.grants(frozenset({"a", "b"}))
    assert not run.grants(frozenset({"a", "c"}))


def test_permits_classification_is_a_ceiling_not_an_equality():
    run = make_run(classification_ceiling=Classification.CONFIDENTIAL)
    assert run.permits_classification(Classification.PUBLIC)
    assert run.permits_classification(Classification.CONFIDENTIAL)
    assert not run.permits_classification(Classification.RESTRICTED)


def test_charge_accumulates_and_leaves_the_original_untouched():
    run = make_run()
    charged = run.charge(steps=1, tokens=500).charge(steps=2, tokens=100)

    assert charged.spend == BudgetSpend(steps=3, tokens=600, spend_micros=0)
    assert run.spend == BudgetSpend()


def test_would_exhaust_names_the_dimension_that_breaks():
    run = make_run(budget=Budget(max_steps=2, max_tokens=100, max_spend_micros=0))

    assert run.would_exhaust(steps=1) is None
    assert run.would_exhaust(steps=3) == "max_steps"
    assert run.would_exhaust(tokens=101) == "max_tokens"
    assert run.would_exhaust(spend_micros=1) == "max_spend_micros"


def test_would_exhaust_reports_a_passed_deadline():
    run = make_run(budget=Budget(deadline_ts=time.time() - 1))
    assert run.would_exhaust() == "deadline"


def test_would_exhaust_counts_what_is_already_spent():
    run = make_run(budget=Budget(max_steps=2)).charge(steps=2)
    assert run.would_exhaust(steps=1) == "max_steps"


def test_charge_does_not_enforce():
    # Accounting and enforcement are split on purpose - charge() past the
    # limit succeeds, and the caller is expected to have asked
    # would_exhaust() first. Pinned so the split is not "fixed" later.
    run = make_run(budget=Budget(max_steps=1)).charge(steps=5)
    assert run.spend.steps == 5


def test_risk_tier_only_rises():
    run = make_run(risk_tier=RiskTier.MEDIUM)

    assert run.raise_risk_tier(RiskTier.HIGH).risk_tier is RiskTier.HIGH
    assert run.raise_risk_tier(RiskTier.LOW).risk_tier is RiskTier.MEDIUM
    # Same tier is a no-op returning the identical object, so callers can
    # apply it unconditionally without churning updated_at.
    assert run.raise_risk_tier(RiskTier.MEDIUM) is run


def test_evidence_and_effect_refs_deduplicate():
    run = make_run().with_evidence("ev-1", "ev-2").with_evidence("ev-2", "ev-3")
    assert run.evidence_refs == ("ev-1", "ev-2", "ev-3")

    run = run.with_effect_intent("eff-1").with_effect_intent("eff-1")
    assert run.effect_intent_ids == ("eff-1",)


def test_with_step_appends_in_order():
    run = make_run()
    run = run.with_step(StepResult(step_name="retrieve", contract_ref=None, ok=True))
    run = run.with_step(StepResult(step_name="draft", contract_ref=None, ok=False))

    assert [s.step_name for s in run.step_results] == ["retrieve", "draft"]
    assert [s.ok for s in run.step_results] == [True, False]


def test_run_is_frozen():
    # Not a style preference: if a workflow step could widen
    # approved_scopes in place, "authority is carried, never generated"
    # would rest on everyone remembering not to.
    run = make_run()
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.approved_scopes = frozenset({"email.send"})  # type: ignore[misc]
