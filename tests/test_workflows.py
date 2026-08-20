"""Workflow template registry and the pre-flight sufficiency check."""

from collections.abc import Sequence

import pytest

from ekassistant.capabilities.contracts import Zone
from ekassistant.workflows.base import StepOutcome, StepSpec, WorkflowStep, check_sufficiency
from ekassistant.workflows.registry import (
    DuplicateWorkflow,
    WorkflowNotFound,
    WorkflowRegistry,
)


class NoopStep:
    spec = StepSpec(name="noop")

    def execute(self, ctx) -> StepOutcome:
        return StepOutcome(run=ctx.run, ok=True)


class SampleWorkflow:
    def __init__(
        self,
        task_type: str = "email_draft",
        version: int = 1,
        zones: frozenset[Zone] = frozenset({Zone.INTERNAL_DATA, Zone.EFFECT}),
        scopes: frozenset[str] = frozenset({"crm.accounts.read", "email.drafts.write"}),
    ):
        self.task_type = task_type
        self.version = version
        self.required_zones = zones
        self.required_scopes = scopes

    def steps(self) -> Sequence[WorkflowStep]:
        return [NoopStep()]


def test_get_resolves_the_latest_version():
    registry = WorkflowRegistry()
    registry.register(SampleWorkflow(version=1))
    registry.register(SampleWorkflow(version=2))

    assert registry.get("email_draft").version == 2
    assert registry.get("email_draft", 1).version == 1


def test_duplicate_registration_raises():
    registry = WorkflowRegistry()
    registry.register(SampleWorkflow())
    with pytest.raises(DuplicateWorkflow):
        registry.register(SampleWorkflow())


def test_unknown_task_type_or_version_raises():
    registry = WorkflowRegistry()
    registry.register(SampleWorkflow())

    with pytest.raises(WorkflowNotFound):
        registry.get("market_research")
    with pytest.raises(WorkflowNotFound):
        registry.get("email_draft", 9)


def test_task_types_are_sorted():
    registry = WorkflowRegistry()
    registry.register(SampleWorkflow("market_research"))
    registry.register(SampleWorkflow("analytics_query"))
    assert registry.task_types() == ["analytics_query", "market_research"]


def test_sufficiency_passes_for_a_matching_run(make_run):
    run = make_run("bob")
    result = check_sufficiency(run, SampleWorkflow())

    assert result.ok
    assert result.reason == "run carries sufficient authority"


def test_sufficiency_reports_every_shortfall_at_once(make_run):
    # A caller told about one missing scope at a time cannot tell that the
    # purpose was simply the wrong one.
    run = make_run("bob", purpose="market_research", task_type="market_research")
    result = check_sufficiency(run, SampleWorkflow())

    assert not result.ok
    assert result.missing_zones == frozenset({Zone.INTERNAL_DATA, Zone.EFFECT})
    assert "missing zones" in result.reason


def test_sufficiency_catches_a_missing_scope(make_run):
    run = make_run("carol")
    result = check_sufficiency(
        run, SampleWorkflow(scopes=frozenset({"email.send", "crm.accounts.read"}))
    )

    assert not result.ok
    assert result.missing_scopes == frozenset({"email.send"})
    assert "email.send" in result.reason
