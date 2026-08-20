"""Shared fixtures for the capability/run/policy tests.

The `policy` fixture loads the *shipped* config/policies.yaml rather than a
purpose-built test file. That is deliberate: the zone separation in that
file is a security control, and a test suite that exercises a synthetic
policy would happily pass while the config actually deployed had a hole in
it. Tests that need a specific rule build their own engine inline.
"""

from pathlib import Path

import pytest

from ekassistant.capabilities.audit import InMemoryAuditSink
from ekassistant.capabilities.bootstrap import build_registry
from ekassistant.capabilities.effects import InMemoryEffectLedger
from ekassistant.capabilities.gateway import CapabilityGateway
from ekassistant.policy.engine import ScopePolicyEngine
from ekassistant.runs.aggregate import Budget
from ekassistant.runs.intake import IntakeRequest, create_run

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Mirrors config/identities.yaml. Duplicated here rather than loaded so a
#: test failure points at the group set the test intended, not at whatever
#: the identity file happens to say today.
GROUPS = {
    "alice": ["engineering", "all-staff"],
    "bob": ["support", "all-staff", "client-comms"],
    "carol": ["finance", "all-staff"],
    "guest": [],
}


@pytest.fixture
def policy() -> ScopePolicyEngine:
    return ScopePolicyEngine.from_yaml(REPO_ROOT / "config" / "policies.yaml")


@pytest.fixture
def registry():
    return build_registry()


@pytest.fixture
def ledger() -> InMemoryEffectLedger:
    return InMemoryEffectLedger()


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture
def gateway(registry, policy, ledger, audit) -> CapabilityGateway:
    return CapabilityGateway(registry, policy, ledger, audit)


@pytest.fixture
def make_run(policy):
    """Build a Run through real intake, never by constructing one directly.

    Tests that hand-build a Run can give it authority intake would have
    refused, which quietly makes the gateway tests weaker than they look.
    """

    def _make(
        principal_id: str = "bob",
        purpose: str = "client_communication",
        task_type: str = "email_draft",
        groups: list[str] | None = None,
        budget: Budget | None = None,
    ):
        return create_run(
            IntakeRequest(
                tenant_id="acme",
                principal_id=principal_id,
                groups=GROUPS[principal_id] if groups is None else groups,
                purpose=purpose,
                task_type=task_type,
                budget=budget or Budget(),
            ),
            policy,
        )

    return _make
