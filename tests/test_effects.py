"""Effect ledger: approvals bind to a payload, not to an intent."""

import pytest
from pydantic import BaseModel

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    ToolContract,
    Zone,
    canonical_hash,
)
from ekassistant.capabilities.effects import (
    ApprovalPayloadMismatch,
    EffectLedgerError,
    EffectNotFound,
    EffectStatus,
    InMemoryEffectLedger,
    authorized_for,
)


class Payload(BaseModel):
    pass


CONTRACT = ToolContract(
    tool="send_email",
    version=1,
    effect_class=EffectClass.IRREVERSIBLE_EXTERNAL,
    zone=Zone.EFFECT,
    required_scopes=frozenset({"email.send"}),
    max_classification=Classification.CONFIDENTIAL,
    input_model=Payload,
    output_model=Payload,
)

PAYLOAD = {"recipients": ["dana.reyes@northwind.example"], "subject": "Q2 update", "body": "Hi"}


def test_canonical_hash_ignores_key_order():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_canonical_hash_changes_with_content():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_propose_records_a_hash_and_summary_not_the_payload():
    ledger = InMemoryEffectLedger()
    intent = ledger.propose(
        "run_1", CONTRACT, PAYLOAD, "idem-1", payload_summary={"recipient_count": 1}
    )

    assert intent.status is EffectStatus.PROPOSED
    assert intent.payload_hash == canonical_hash(PAYLOAD)
    assert intent.payload_summary == {"recipient_count": 1}
    # The body must not be recoverable from the ledger record.
    assert "body" not in intent.payload_summary
    assert not hasattr(intent, "payload")


def test_approve_binds_the_exact_payload():
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")
    approved = ledger.approve(intent.intent_id, "carol", PAYLOAD, reason="checked the numbers")

    assert approved.status is EffectStatus.APPROVED
    assert approved.approval.approved_by == "carol"
    assert approved.approval.payload_hash == canonical_hash(PAYLOAD)


def test_approving_a_payload_that_already_drifted_raises():
    # The approver gets the error, because the approver is the only one
    # who can resolve it.
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")

    with pytest.raises(ApprovalPayloadMismatch):
        ledger.approve(intent.intent_id, "carol", {**PAYLOAD, "subject": "Q3 update"})


def test_an_edit_after_approval_invalidates_it():
    # The central control: approving "send this" and then sending
    # something else must be impossible without a fresh approval.
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")
    approved = ledger.approve(intent.intent_id, "carol", PAYLOAD)

    edited = {**PAYLOAD, "recipients": ["someone.else@elsewhere.example"]}
    ok, reason = authorized_for(approved, edited, approval_required=True)

    assert not ok
    assert "does not match the approved hash" in reason


def test_unapproved_intent_does_not_authorize_when_approval_is_required():
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")

    ok, reason = authorized_for(intent, PAYLOAD, approval_required=True)
    assert not ok
    assert "requires approval" in reason

    # A reversible write needs the ledger entry but not a human.
    ok, _ = authorized_for(intent, PAYLOAD, approval_required=False)
    assert ok


def test_a_committed_intent_never_authorizes_again():
    # This is what stops a retried model call from sending a second email.
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")
    ledger.approve(intent.intent_id, "carol", PAYLOAD)
    committed = ledger.mark_committed(intent.intent_id, "msg_abc123")

    assert committed.status is EffectStatus.COMMITTED
    assert committed.provider_ref == "msg_abc123"

    ok, reason = authorized_for(committed, PAYLOAD, approval_required=True)
    assert not ok
    assert "already" in reason


def test_settled_intents_reject_further_transitions():
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")
    ledger.approve(intent.intent_id, "carol", PAYLOAD)
    ledger.mark_committed(intent.intent_id, "msg_abc123")

    for action in (
        lambda: ledger.approve(intent.intent_id, "carol", PAYLOAD),
        lambda: ledger.mark_committed(intent.intent_id, "msg_other"),
        lambda: ledger.mark_failed(intent.intent_id, "late failure"),
        lambda: ledger.reject(intent.intent_id, "carol", "changed my mind"),
    ):
        with pytest.raises(EffectLedgerError):
            action()


def test_rejection_is_terminal_too():
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")
    rejected = ledger.reject(intent.intent_id, "carol", "wrong recipient")

    assert rejected.status is EffectStatus.REJECTED
    assert "wrong recipient" in rejected.detail
    with pytest.raises(EffectLedgerError):
        ledger.approve(intent.intent_id, "carol", PAYLOAD)


def test_failed_intent_can_still_be_retried():
    # FAILED is not settled - a transport error leaves the effect
    # re-attemptable, unlike a commit.
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")
    ledger.approve(intent.intent_id, "carol", PAYLOAD)
    ledger.mark_failed(intent.intent_id, "provider timeout")

    assert ledger.mark_committed(intent.intent_id, "msg_late").status is EffectStatus.COMMITTED


def test_find_committed_matches_only_committed_intents():
    ledger = InMemoryEffectLedger()
    intent = ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")

    assert ledger.find_committed("idem-1") is None
    ledger.approve(intent.intent_id, "carol", PAYLOAD)
    assert ledger.find_committed("idem-1") is None

    ledger.mark_committed(intent.intent_id, "msg_abc123")
    assert ledger.find_committed("idem-1").intent_id == intent.intent_id


def test_for_run_scopes_by_run():
    ledger = InMemoryEffectLedger()
    ledger.propose("run_1", CONTRACT, PAYLOAD, "idem-1")
    ledger.propose("run_2", CONTRACT, PAYLOAD, "idem-2")

    assert [i.run_id for i in ledger.for_run("run_1")] == ["run_1"]


def test_unknown_intent_raises():
    with pytest.raises(EffectNotFound):
        InMemoryEffectLedger().get("eff_missing")
