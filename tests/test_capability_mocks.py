"""Mock capabilities: they enforce what their real counterparts will."""

import pytest

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    InvocationContext,
    Zone,
)
from ekassistant.capabilities.mocks.crm import MockCrmAccountLookup
from ekassistant.capabilities.mocks.email import MockDraftStore, MockEmailSend, SendRefused
from ekassistant.capabilities.mocks.metrics import MockCertifiedMetric
from ekassistant.capabilities.mocks.web import INJECTION_SNIPPET, MockWebSearch


def ctx(idempotency_key: str | None = None) -> InvocationContext:
    return InvocationContext(
        run_id="run_1",
        tenant_id="acme",
        principal_id="bob",
        purpose="client_communication",
        granted_scopes=frozenset(),
        limits=MockCrmAccountLookup.contract.limits,
        idempotency_key=idempotency_key,
    )


def test_every_mock_declares_provenance_and_its_origin_zone(registry):
    for contract in registry.all_contracts():
        assert contract.required_scopes, contract.ref
        assert contract.description, contract.ref


def test_crm_lookup_returns_authorized_contacts():
    result = MockCrmAccountLookup().invoke({"account_id": "acct-1001"}, ctx())

    emails = [c["email"] for c in result.data["authorized_contacts"]]
    assert emails == ["dana.reyes@northwind.example", "sam.okafor@northwind.example"]
    assert result.provenance.classification is Classification.CONFIDENTIAL
    assert result.provenance.source_ref == "crm://accounts/acct-1001"


def test_crm_lookup_raises_rather_than_returning_an_empty_account():
    # An empty record would read downstream as "no authorized contacts",
    # which is a permission answer, not a lookup failure.
    with pytest.raises(KeyError):
        MockCrmAccountLookup().invoke({"account_id": "acct-9999"}, ctx())


def test_certified_metric_returns_version_and_freshness():
    result = MockCertifiedMetric().invoke(
        {"metric_id": "ticket_acceptance_rate", "period": "2026-Q2"}, ctx()
    )

    assert result.data["value"] == 0.873
    assert result.data["unit"] == "ratio"
    assert result.data["metric_version"] == "2.0"
    assert result.data["source_query_id"]
    assert result.provenance.freshness_time is not None


def test_certified_metric_refuses_an_uncertified_period():
    # Refusal rather than interpolation: a confident number for a period
    # nobody certified is worse than no number.
    with pytest.raises(KeyError):
        MockCertifiedMetric().invoke(
            {"metric_id": "ticket_acceptance_rate", "period": "2027-Q4"}, ctx()
        )


def test_web_search_ships_a_hostile_fixture():
    # Present from Phase 0 so "web content can never name a tool or a
    # recipient" stays a constraint the later workflow is written against.
    result = MockWebSearch().invoke({"query": "market outlook"}, ctx())
    snippets = [hit["snippet"] for hit in result.data["hits"]]

    assert INJECTION_SNIPPET in snippets
    assert "send_email" in INJECTION_SNIPPET
    assert result.is_untrusted


def test_web_search_is_public_but_still_untrusted():
    # Low sensitivity and low trust are different axes.
    result = MockWebSearch().invoke({"query": "x"}, ctx())
    assert result.provenance.classification is Classification.PUBLIC
    assert result.origin_zone is Zone.WEB_RESEARCH
    assert result.is_untrusted


def test_web_search_respects_max_results():
    result = MockWebSearch().invoke({"query": "x", "max_results": 1}, ctx())
    assert len(result.data["hits"]) == 1


def test_draft_store_versions_by_run():
    store = MockDraftStore()
    payload = {
        "account_id": "acct-1001",
        "subject": "Update",
        "body": "Hello",
        "recipients": ["dana.reyes@northwind.example"],
    }

    assert store.invoke(payload, ctx()).data["version"] == 1
    assert store.invoke(payload, ctx()).data["version"] == 2
    assert store.contract.effect_class is EffectClass.REVERSIBLE_WRITE


def test_send_is_idempotent_under_the_same_key():
    sender = MockEmailSend()
    payload = {
        "draft_id": "draft_1",
        "subject": "Update",
        "body": "Hello",
        "recipients": ["dana.reyes@northwind.example"],
    }

    first = sender.invoke(payload, ctx("send-1"))
    second = sender.invoke(payload, ctx("send-1"))

    assert first.data["provider_message_id"] == second.data["provider_message_id"]
    assert len(sender.outbox) == 1


def test_send_requires_an_idempotency_key():
    with pytest.raises(SendRefused):
        MockEmailSend().invoke(
            {
                "draft_id": "d",
                "subject": "s",
                "body": "b",
                "recipients": ["dana.reyes@northwind.example"],
            },
            ctx(),
        )


def test_kill_switch_refuses_sends():
    sender = MockEmailSend()
    sender.enabled = False
    with pytest.raises(SendRefused):
        sender.invoke(
            {
                "draft_id": "d",
                "subject": "s",
                "body": "b",
                "recipients": ["dana.reyes@northwind.example"],
            },
            ctx("send-1"),
        )
    assert sender.outbox == []
