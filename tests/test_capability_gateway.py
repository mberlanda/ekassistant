"""CapabilityGateway: every deny path, and the effect/approval flow.

The allow cases here are the short section. The point of this file is the
refusals - a gateway that permits the right things is easy, and a gateway
that refuses the wrong ones under adversarial shapes is the actual
requirement.
"""

import pytest

from ekassistant.capabilities.contracts import Zone
from ekassistant.capabilities.effects import EffectStatus
from ekassistant.capabilities.gateway import (
    CapabilityDenied,
    CapabilityExecutionError,
)
from ekassistant.runs.aggregate import Budget, RunStatus
from ekassistant.runs.machine import transition

DRAFT = {
    "account_id": "acct-1001",
    "subject": "Q2 service update",
    "body": "Volumes are up 4% this quarter.",
    "recipients": ["dana.reyes@northwind.example"],
}
SEND = {
    "draft_id": "draft_1",
    "subject": "Q2 service update",
    "body": "Volumes are up 4% this quarter.",
    "recipients": ["dana.reyes@northwind.example"],
}


# -- allow paths -----------------------------------------------------


def test_read_only_invocation_succeeds_and_is_audited(gateway, make_run, audit):
    run = make_run()
    outcome = gateway.invoke(run, "crm_account_lookup", {"account_id": "acct-1001"})

    assert outcome.allowed
    result = outcome.unwrap()
    assert result.data["account_name"] == "Northwind Logistics"
    assert result.origin_zone is Zone.INTERNAL_DATA
    assert not result.is_untrusted

    record = audit.records[-1]
    assert record.allowed
    assert record.contract_ref == "crm_account_lookup@1"
    assert record.result_hash
    # Identifiers and hashes only - never the payload.
    assert not hasattr(record, "payload")


def test_web_results_are_labelled_untrusted(gateway, make_run):
    run = make_run("bob", purpose="market_research", task_type="market_research")
    result = gateway.invoke(run, "web_search", {"query": "logistics demand"}).unwrap()

    assert result.is_untrusted
    assert result.origin_zone is Zone.WEB_RESEARCH


def test_visible_contracts_reflect_the_runs_zones(gateway, make_run):
    email_run = make_run()
    research_run = make_run("bob", purpose="market_research", task_type="market_research")

    email_tools = {c.tool for c in gateway.visible_contracts(email_run)}
    research_tools = {c.tool for c in gateway.visible_contracts(research_run)}

    assert {"crm_account_lookup", "save_email_draft", "send_email"} <= email_tools
    assert "web_search" not in email_tools

    assert research_tools == {"web_search"}


# -- authority denials -----------------------------------------------


def test_zone_denial_even_when_the_scope_is_held(gateway, make_run):
    # bob holds crm.accounts.read, but a research purpose carries no
    # INTERNAL_DATA zone. The scope is irrelevant.
    run = make_run("bob", purpose="market_research", task_type="market_research")
    outcome = gateway.invoke(run, "crm_account_lookup", {"account_id": "acct-1001"})

    assert not outcome.allowed
    assert "zone" in outcome.reason


def test_scope_denial_drafting_is_not_sending(gateway, make_run):
    # carol can draft but is not in client-comms, so cannot send.
    run = make_run("carol")
    outcome = gateway.invoke(run, "send_email", SEND)

    assert not outcome.allowed
    assert "email.send" in outcome.reason


def test_classification_ceiling_denial(gateway, make_run):
    # certified_metric handles RESTRICTED data; client_communication tops
    # out at CONFIDENTIAL.
    run = make_run("carol")
    outcome = gateway.invoke(
        run, "certified_metric", {"metric_id": "revenue_by_account", "period": "2026-Q2"}
    )

    assert not outcome.allowed
    assert "RESTRICTED" in outcome.reason


def test_analytics_purpose_can_reach_restricted_data(gateway, make_run):
    run = make_run("carol", purpose="internal_analytics", task_type="analytics_query")
    result = gateway.invoke(
        run, "certified_metric", {"metric_id": "revenue_by_account", "period": "2026-Q2"}
    ).unwrap()

    assert result.data["value"] == 482_300.0
    assert result.data["metric_version"] == "3.1"


def test_unknown_capability_is_denied(gateway, make_run):
    outcome = gateway.invoke(make_run(), "exfiltrate_everything", {})
    assert not outcome.allowed
    assert "unknown capability" in outcome.reason


def test_terminal_run_cannot_invoke_anything(gateway, make_run):
    run = transition(make_run(), RunStatus.CANCELLED, reason="user cancelled")
    outcome = gateway.invoke(run, "crm_account_lookup", {"account_id": "acct-1001"})

    assert not outcome.allowed
    assert "terminal" in outcome.reason


def test_malformed_payload_is_denied_before_the_tool_runs(gateway, make_run):
    outcome = gateway.invoke(make_run(), "crm_account_lookup", {"account_id": ""})
    assert not outcome.allowed
    assert "input schema" in outcome.reason


# -- budget ----------------------------------------------------------


def test_budget_is_charged_and_then_exhausts(gateway, make_run):
    run = make_run(budget=Budget(max_steps=1))

    first = gateway.invoke(run, "crm_account_lookup", {"account_id": "acct-1001"})
    assert first.allowed
    assert first.run.spend.steps == 1

    second = gateway.invoke(first.run, "crm_account_lookup", {"account_id": "acct-1001"})
    assert not second.allowed
    assert "budget exhausted: max_steps" in second.reason


def test_denied_attempts_still_consume_budget(gateway, make_run):
    # A model looping on a forbidden tool must run out, not loop for free.
    run = make_run(budget=Budget(max_steps=3))
    for _ in range(3):
        outcome = gateway.invoke(run, "no_such_tool", {})
        assert not outcome.allowed
        run = outcome.run

    assert run.spend.steps == 3
    assert gateway.invoke(run, "crm_account_lookup", {"account_id": "acct-1001"}).reason.startswith(
        "budget exhausted"
    )


# -- effects and approval --------------------------------------------


def test_reversible_write_needs_an_intent_but_no_approval(gateway, make_run, ledger):
    run = make_run()

    denied = gateway.invoke(run, "save_email_draft", DRAFT)
    assert not denied.allowed
    assert "requires a recorded effect intent" in denied.reason

    intent = gateway.propose_effect(run, "save_email_draft", DRAFT, idempotency_key="draft-1")
    outcome = gateway.invoke(
        denied.run, "save_email_draft", DRAFT, effect_intent_id=intent.intent_id
    )

    assert outcome.allowed
    assert ledger.get(intent.intent_id).status is EffectStatus.COMMITTED
    assert intent.intent_id in outcome.run.effect_intent_ids


def test_irreversible_send_requires_an_approval(gateway, make_run, ledger):
    run = make_run("bob")
    intent = gateway.propose_effect(run, "send_email", SEND, idempotency_key="send-1")

    unapproved = gateway.invoke(run, "send_email", SEND, effect_intent_id=intent.intent_id)
    assert not unapproved.allowed
    assert "requires approval" in unapproved.reason

    ledger.approve(intent.intent_id, "bob", gateway.canonical_payload("send_email", SEND))
    approved = gateway.invoke(
        unapproved.run, "send_email", SEND, effect_intent_id=intent.intent_id
    )

    assert approved.allowed
    assert approved.unwrap().data["provider_message_id"].startswith("msg_")
    assert ledger.get(intent.intent_id).status is EffectStatus.COMMITTED


def test_editing_the_payload_after_approval_blocks_the_send(gateway, make_run, ledger, registry):
    # The control that matters most: approval is bound to a payload, so a
    # changed recipient cannot ride an existing approval.
    run = make_run("bob")
    intent = gateway.propose_effect(run, "send_email", SEND, idempotency_key="send-1")
    ledger.approve(intent.intent_id, "bob", gateway.canonical_payload("send_email", SEND))

    tampered = {**SEND, "recipients": ["analyst@competitor.example"]}
    outcome = gateway.invoke(run, "send_email", tampered, effect_intent_id=intent.intent_id)

    assert not outcome.allowed
    assert "does not match the approved hash" in outcome.reason
    assert registry.get("send_email").outbox == []


def test_an_intent_from_another_run_is_refused(gateway, make_run, ledger):
    run = make_run("bob")
    other = make_run("bob")
    intent = gateway.propose_effect(other, "send_email", SEND, idempotency_key="send-1")
    ledger.approve(intent.intent_id, "bob", gateway.canonical_payload("send_email", SEND))

    outcome = gateway.invoke(run, "send_email", SEND, effect_intent_id=intent.intent_id)
    assert not outcome.allowed
    assert "belongs to another run" in outcome.reason


def test_an_intent_for_a_different_contract_is_refused(gateway, make_run, ledger):
    run = make_run("bob")
    intent = gateway.propose_effect(run, "save_email_draft", DRAFT, idempotency_key="draft-1")

    outcome = gateway.invoke(run, "send_email", SEND, effect_intent_id=intent.intent_id)
    assert not outcome.allowed
    assert "was proposed for" in outcome.reason


def test_unknown_intent_is_refused(gateway, make_run):
    outcome = gateway.invoke(make_run("bob"), "send_email", SEND, effect_intent_id="eff_nope")
    assert not outcome.allowed
    assert "unknown effect intent" in outcome.reason


def test_a_committed_idempotency_key_blocks_a_second_send(gateway, make_run, ledger, registry):
    run = make_run("bob")
    canonical = gateway.canonical_payload("send_email", SEND)

    first = gateway.propose_effect(run, "send_email", SEND, idempotency_key="send-1")
    ledger.approve(first.intent_id, "bob", canonical)
    sent = gateway.invoke(run, "send_email", SEND, effect_intent_id=first.intent_id)
    assert sent.allowed

    # A second intent under the same key - the retried-send shape.
    second = gateway.propose_effect(run, "send_email", SEND, idempotency_key="send-1")
    ledger.approve(second.intent_id, "bob", canonical)
    outcome = gateway.invoke(sent.run, "send_email", SEND, effect_intent_id=second.intent_id)

    assert not outcome.allowed
    assert "already committed" in outcome.reason
    assert len(registry.get("send_email").outbox) == 1


def test_a_read_only_capability_rejects_an_effect_intent(gateway, make_run):
    run = make_run()
    outcome = gateway.invoke(
        run, "crm_account_lookup", {"account_id": "acct-1001"}, effect_intent_id="eff_anything"
    )
    assert not outcome.allowed
    assert "takes no effect intent" in outcome.reason


def test_propose_effect_refuses_a_read_only_contract(gateway, make_run):
    with pytest.raises(CapabilityDenied):
        gateway.propose_effect(
            make_run(), "crm_account_lookup", {"account_id": "acct-1001"}, idempotency_key="k"
        )


def test_kill_switch_stops_sending_without_stopping_drafting(gateway, make_run, ledger, registry):
    run = make_run("bob")
    registry.get("send_email").enabled = False

    intent = gateway.propose_effect(run, "send_email", SEND, idempotency_key="send-1")
    ledger.approve(intent.intent_id, "bob", gateway.canonical_payload("send_email", SEND))

    with pytest.raises(CapabilityExecutionError):
        gateway.invoke(run, "send_email", SEND, effect_intent_id=intent.intent_id)

    draft_intent = gateway.propose_effect(
        run, "save_email_draft", DRAFT, idempotency_key="draft-1"
    )
    assert gateway.invoke(
        run, "save_email_draft", DRAFT, effect_intent_id=draft_intent.intent_id
    ).allowed


# -- execution failure -----------------------------------------------


def test_a_failing_capability_raises_but_keeps_the_charge_and_the_audit(
    gateway, make_run, audit
):
    run = make_run()
    with pytest.raises(CapabilityExecutionError) as exc:
        gateway.invoke(run, "crm_account_lookup", {"account_id": "acct-does-not-exist"})

    assert exc.value.run.spend.steps == 1
    assert exc.value.contract_ref == "crm_account_lookup@1"

    record = audit.records[-1]
    assert record.allowed  # authorized, then failed - a distinct outcome
    assert "KeyError" in record.reason


def test_an_uncertain_send_leaves_the_intent_unsettled_for_reconciliation(
    gateway, make_run, ledger, registry
):
    # A raising send does NOT mean nothing happened. Marking the intent
    # FAILED would assert an outcome nothing at this layer knows.
    run = make_run("bob")
    registry.get("send_email").enabled = False
    intent = gateway.propose_effect(run, "send_email", SEND, idempotency_key="send-1")
    ledger.approve(intent.intent_id, "bob", gateway.canonical_payload("send_email", SEND))

    with pytest.raises(CapabilityExecutionError):
        gateway.invoke(run, "send_email", SEND, effect_intent_id=intent.intent_id)

    assert ledger.get(intent.intent_id).status is EffectStatus.APPROVED


# -- outcome ergonomics ----------------------------------------------


def test_unwrap_raises_on_a_denial(gateway, make_run):
    outcome = gateway.invoke(make_run(), "no_such_tool", {})
    with pytest.raises(CapabilityDenied):
        outcome.unwrap()
