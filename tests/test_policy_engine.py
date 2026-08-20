"""ScopePolicyEngine: the shipped config, and each individual deny path."""

import pytest
from pydantic import BaseModel

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    ToolContract,
    Zone,
)
from ekassistant.policy.engine import PolicyConfigError, PolicyRequest, ScopePolicyEngine


class Payload(BaseModel):
    pass


def contract(
    tool: str = "t",
    effect_class: EffectClass = EffectClass.READ_ONLY,
    zone: Zone = Zone.INTERNAL_DATA,
    scopes: frozenset[str] = frozenset({"crm.accounts.read"}),
    classification: Classification = Classification.CONFIDENTIAL,
) -> ToolContract:
    return ToolContract(
        tool=tool,
        version=1,
        effect_class=effect_class,
        zone=zone,
        required_scopes=scopes,
        max_classification=classification,
        input_model=Payload,
        output_model=Payload,
    )


def req(policy_contract: ToolContract, **kwargs) -> PolicyRequest:
    defaults = dict(
        tenant_id="acme",
        principal_id="bob",
        purpose="client_communication",
        task_type="email_draft",
        granted_scopes=frozenset({"crm.accounts.read"}),
    )
    return PolicyRequest(contract=policy_contract, **{**defaults, **kwargs})


def test_shipped_config_parses(policy):
    assert policy.policy_version
    assert policy.profile_for("client_communication") is not None
    assert policy.profile_for("market_research") is not None


def test_shipped_config_keeps_the_three_capabilities_apart(policy):
    # The single most important assertion in this suite. No purpose may
    # combine private data, untrusted web content and an outbound channel.
    for name in ("knowledge_qa", "client_communication", "market_research", "internal_analytics"):
        zones = policy.profile_for(name).allowed_zones
        combined = {Zone.INTERNAL_DATA, Zone.WEB_RESEARCH, Zone.EFFECT} <= zones
        assert not combined, f"purpose {name} combines all three risk surfaces"

    research = policy.profile_for("market_research").allowed_zones
    assert Zone.INTERNAL_DATA not in research
    assert Zone.EFFECT not in research

    email = policy.profile_for("client_communication").allowed_zones
    assert Zone.WEB_RESEARCH not in email


def test_scopes_for_groups_unions_and_ignores_unknown_groups(policy):
    granted = policy.scopes_for_groups(["support", "all-staff", "not-a-real-group"])
    assert "crm.accounts.read" in granted
    assert "web.search" in granted
    assert "email.send" not in granted


def test_no_groups_grants_no_scopes(policy):
    assert policy.scopes_for_groups([]) == frozenset()


def test_allow_carries_the_policy_version(policy):
    decision = policy.evaluate(req(contract()))
    assert decision.allowed
    assert decision.policy_version == policy.policy_version
    assert decision.reason


def test_unknown_purpose_denies(policy):
    decision = policy.evaluate(req(contract(), purpose="nope"))
    assert not decision.allowed
    assert "unknown purpose" in decision.reason


def test_task_type_outside_the_purpose_denies(policy):
    decision = policy.evaluate(req(contract(), task_type="analytics_query"))
    assert not decision.allowed
    assert "task type" in decision.reason


def test_zone_outside_the_purpose_denies(policy):
    decision = policy.evaluate(req(contract(zone=Zone.WEB_RESEARCH, scopes=frozenset())))
    assert not decision.allowed
    assert "zone" in decision.reason


def test_effect_class_outside_the_purpose_denies(policy):
    # A write in the web zone: the zone itself is permitted for
    # market_research, so this isolates the effect-class check rather than
    # tripping the zone check first.
    decision = policy.evaluate(
        req(
            contract(zone=Zone.WEB_RESEARCH, effect_class=EffectClass.HIGH_IMPACT_WRITE),
            purpose="market_research",
            task_type="market_research",
        )
    )
    assert not decision.allowed
    assert "effect class" in decision.reason


def test_classification_above_the_purpose_ceiling_denies(policy):
    # client_communication tops out at CONFIDENTIAL.
    decision = policy.evaluate(req(contract(classification=Classification.RESTRICTED)))
    assert not decision.allowed
    assert "RESTRICTED" in decision.reason


def test_missing_scopes_deny_and_are_named(policy):
    decision = policy.evaluate(
        req(contract(scopes=frozenset({"crm.accounts.read", "email.send"})))
    )
    assert not decision.allowed
    assert "email.send" in decision.reason


def test_unknown_zone_name_in_config_fails_loudly(tmp_path):
    path = tmp_path / "policies.yaml"
    path.write_text(
        "policy_version: '1'\n"
        "purposes:\n"
        "  p:\n"
        "    allowed_task_types: [t]\n"
        "    allowed_zones: [NOT_A_ZONE]\n"
    )
    with pytest.raises(PolicyConfigError) as exc:
        ScopePolicyEngine.from_yaml(path)
    assert "NOT_A_ZONE" in str(exc.value)


def test_unknown_classification_in_config_fails_loudly(tmp_path):
    path = tmp_path / "policies.yaml"
    path.write_text(
        "policy_version: '1'\n"
        "purposes:\n"
        "  p:\n"
        "    allowed_task_types: [t]\n"
        "    max_classification: TOP_SECRET\n"
    )
    with pytest.raises(PolicyConfigError) as exc:
        ScopePolicyEngine.from_yaml(path)
    assert "TOP_SECRET" in str(exc.value)


def test_empty_config_yields_a_engine_that_permits_nothing(tmp_path):
    path = tmp_path / "policies.yaml"
    path.write_text("policy_version: '9'\n")
    engine = ScopePolicyEngine.from_yaml(path)

    assert engine.profile_for("client_communication") is None
    assert engine.scopes_for_groups(["support"]) == frozenset()
    assert not engine.evaluate(req(contract())).allowed
