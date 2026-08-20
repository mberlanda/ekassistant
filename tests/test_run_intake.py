"""Intake: authority is derived from policy, never supplied by the caller."""

import dataclasses

import pytest

from ekassistant.capabilities.contracts import Classification, Zone
from ekassistant.runs.aggregate import RunStatus
from ekassistant.runs.intake import IntakeRejected, IntakeRequest, create_run


def request(**kwargs) -> IntakeRequest:
    defaults = dict(
        tenant_id="acme",
        principal_id="bob",
        groups=["support", "all-staff", "client-comms"],
        purpose="client_communication",
        task_type="email_draft",
    )
    return IntakeRequest(**{**defaults, **kwargs})


def test_intake_request_cannot_carry_authority():
    # Structural, not behavioural: if IntakeRequest ever grows an
    # approved_scopes/allowed_zones field, a caller could hand a Run
    # authority policy never granted, and every downstream check would
    # still pass. Guarded here because nothing else would catch it.
    fields = {f.name for f in dataclasses.fields(IntakeRequest)}
    assert "approved_scopes" not in fields
    assert "allowed_zones" not in fields
    assert "classification_ceiling" not in fields


def test_scopes_are_the_union_of_group_grants(policy):
    run = create_run(request(), policy)
    assert {"crm.accounts.read", "email.drafts.write", "email.send"} <= run.approved_scopes


def test_zones_and_ceiling_come_from_the_purpose(policy):
    run = create_run(request(), policy)
    assert run.allowed_zones == frozenset({Zone.PURE, Zone.INTERNAL_DATA, Zone.EFFECT})
    assert run.classification_ceiling is Classification.CONFIDENTIAL
    assert run.status is RunStatus.RECEIVED


def test_unknown_purpose_is_rejected(policy):
    with pytest.raises(IntakeRejected) as exc:
        create_run(request(purpose="do_anything"), policy)
    assert not exc.value.decision.allowed
    assert "unknown purpose" in exc.value.decision.reason


def test_task_type_must_match_the_purpose(policy):
    with pytest.raises(IntakeRejected) as exc:
        create_run(request(task_type="market_research"), policy)
    assert "not permitted for purpose" in exc.value.decision.reason


def test_guest_gets_a_run_that_can_do_nothing(policy):
    run = create_run(request(principal_id="guest", groups=[]), policy)
    assert run.approved_scopes == frozenset()


def test_the_same_person_gets_less_authority_for_a_research_purpose(policy):
    # bob holds email.send as a group grant. Declaring a research purpose
    # must not produce a Run that can reach the effect zone with it - this
    # is the separation that keeps untrusted web content away from an
    # outbound channel.
    research = create_run(
        request(purpose="market_research", task_type="market_research"), policy
    )

    assert Zone.EFFECT not in research.allowed_zones
    assert Zone.INTERNAL_DATA not in research.allowed_zones
    assert research.classification_ceiling is Classification.PUBLIC
    # The scope is still held; the zone is what stops it being usable.
    assert "email.send" in research.approved_scopes


def test_analytics_purpose_is_the_only_one_reaching_restricted(policy):
    analytics = create_run(
        request(
            principal_id="carol",
            groups=["finance", "all-staff"],
            purpose="internal_analytics",
            task_type="analytics_query",
        ),
        policy,
    )
    assert analytics.classification_ceiling is Classification.RESTRICTED
    assert Zone.EFFECT not in analytics.allowed_zones
    assert Zone.WEB_RESEARCH not in analytics.allowed_zones


def test_policy_version_is_stamped_on_the_run(policy):
    assert create_run(request(), policy).policy_version == policy.policy_version
