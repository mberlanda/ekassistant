from ekassistant.models.context import ContextChunk
from ekassistant.models.generation import (
    REASON_BLANK_ANSWER,
    REASON_EMPTY_CONTEXT,
    REASON_INVALID_CITATION,
    REASON_MALFORMED_RESPONSE,
    REASON_MODEL_REPORTED_ABSTAIN,
    REASON_NO_CITATIONS,
    generate_answer,
)

CONTEXT = [ContextChunk(chunk_id="c1", source="policy.md", text="The VPN requires MFA.")]


class FakeChatClient:
    def __init__(self, response: str):
        self._response = response

    def chat_json(self, system: str, user: str, json_schema: dict) -> str:
        return self._response


def test_no_context_abstains_without_calling_the_model():
    client = FakeChatClient(response="should never be read")
    result = generate_answer("does the vpn need mfa?", [], client)
    assert result.abstained is True
    assert result.answer == ""
    assert result.citations == []
    assert result.reason == REASON_EMPTY_CONTEXT


def test_valid_grounded_answer_passes_through():
    response = (
        '{"answer": "Yes, MFA is required.", '
        '"citations": [{"chunk_id": "c1", "source": "policy.md"}], "abstained": false}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is False
    assert result.answer == "Yes, MFA is required."
    assert result.citations[0].chunk_id == "c1"
    assert result.reason is None


def test_citation_outside_context_downgrades_to_abstain():
    response = (
        '{"answer": "Yes.", '
        '"citations": [{"chunk_id": "does-not-exist", "source": "policy.md"}], '
        '"abstained": false}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_INVALID_CITATION


def test_no_citations_but_not_abstained_downgrades_to_abstain():
    response = '{"answer": "Yes.", "citations": [], "abstained": false}'
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_NO_CITATIONS


def test_malformed_json_downgrades_to_abstain():
    client = FakeChatClient(response="not valid json at all")
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_MALFORMED_RESPONSE


def test_model_reported_abstain_is_normalized():
    response = '{"answer": "irrelevant text", "citations": [], "abstained": true}'
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.answer == ""
    assert result.citations == []
    assert result.reason == REASON_MODEL_REPORTED_ABSTAIN


def test_mutating_one_abstain_result_does_not_affect_the_next():
    client = FakeChatClient(response="not valid json at all")
    first = generate_answer("q1", CONTEXT, client)
    first.citations.append(object())  # simulate a careless caller mutating it
    second = generate_answer("q2", CONTEXT, client)
    assert second.citations == []


def test_blank_answer_but_not_abstained_downgrades_to_abstain():
    response = (
        '{"answer": "", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_BLANK_ANSWER


def test_citation_source_is_rebuilt_from_context_not_trusted_from_the_model():
    # A citation with a valid chunk_id but a source that doesn't match that
    # chunk's real source in the provided context - the model may have
    # echoed a fabricated or injected source label. The real source from
    # CONTEXT must win, not whatever the model claimed.
    response = (
        '{"answer": "Yes.", '
        '"citations": [{"chunk_id": "c1", "source": "attacker-controlled.md"}], '
        '"abstained": false}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is False
    assert result.citations[0].source == "policy.md"


def test_underlying_client_error_propagates_instead_of_being_swallowed():
    class RaisingChatClient:
        def chat_json(self, system: str, user: str, json_schema: dict) -> str:
            raise ConnectionError("ollama is not reachable")

    try:
        generate_answer("does the vpn need mfa?", CONTEXT, RaisingChatClient())
    except ConnectionError:
        pass
    else:
        raise AssertionError("expected the transport error to propagate")
