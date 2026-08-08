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
        self.temperatures_seen: list[float] = []

    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
        self.temperatures_seen.append(temperature)
        return self._response


def test_no_context_abstains_without_calling_the_model():
    client = FakeChatClient(response="should never be read")
    result = generate_answer("does the vpn need mfa?", [], client)
    assert result.abstained is True
    assert result.answer == ""
    assert result.citations == []
    assert result.reason == REASON_EMPTY_CONTEXT
    assert result.confidence is None


def test_valid_grounded_answer_passes_through():
    response = (
        '{"answer": "Yes, MFA is required.", '
        '"citations": [{"chunk_id": "c1", "source": "policy.md"}], "abstained": false, '
        '"confidence": 0.9}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is False
    assert result.answer == "Yes, MFA is required."
    assert result.citations[0].chunk_id == "c1"
    assert result.reason is None
    assert result.confidence == 0.9


def test_generation_temperature_is_passed_through_to_the_chat_client():
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.5}'
    )
    client = FakeChatClient(response)
    generate_answer("does the vpn need mfa?", CONTEXT, client, temperature=0.7)
    assert client.temperatures_seen == [0.7]


def test_citation_outside_context_downgrades_to_abstain():
    response = (
        '{"answer": "Yes.", '
        '"citations": [{"chunk_id": "does-not-exist", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.8}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_INVALID_CITATION
    # The model's self-reported confidence about an untrusted response
    # isn't trusted either - see GroundedAnswer.confidence's docstring.
    assert result.confidence is None


def test_no_citations_but_not_abstained_downgrades_to_abstain():
    response = '{"answer": "Yes.", "citations": [], "abstained": false, "confidence": 0.8}'
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_NO_CITATIONS
    assert result.confidence is None


def test_malformed_json_downgrades_to_abstain():
    client = FakeChatClient(response="not valid json at all")
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_MALFORMED_RESPONSE
    assert result.confidence is None


def test_a_response_omitting_confidence_still_succeeds_with_confidence_none():
    # confidence is deliberately NOT a required field (see
    # ModelResponse.confidence's docstring: making it required measurably
    # pushed the small local model toward abstaining far more often, even
    # at low temperature) - a response that omits it entirely is valid,
    # just reports no confidence, rather than being downgraded to abstain.
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is False
    assert result.confidence is None


def test_an_out_of_range_confidence_downgrades_to_abstain():
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 1.5}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_MALFORMED_RESPONSE


def test_model_reported_abstain_is_normalized():
    response = (
        '{"answer": "irrelevant text", "citations": [], "abstained": true, "confidence": 0.1}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.answer == ""
    assert result.citations == []
    assert result.reason == REASON_MODEL_REPORTED_ABSTAIN
    assert result.confidence is None


def test_mutating_one_abstain_result_does_not_affect_the_next():
    client = FakeChatClient(response="not valid json at all")
    first = generate_answer("q1", CONTEXT, client)
    first.citations.append(object())  # simulate a careless caller mutating it
    second = generate_answer("q2", CONTEXT, client)
    assert second.citations == []


def test_blank_answer_but_not_abstained_downgrades_to_abstain():
    response = (
        '{"answer": "", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.6}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_BLANK_ANSWER
    assert result.confidence is None


def test_citation_source_is_rebuilt_from_context_not_trusted_from_the_model():
    # A citation with a valid chunk_id but a source that doesn't match that
    # chunk's real source in the provided context - the model may have
    # echoed a fabricated or injected source label. The real source from
    # CONTEXT must win, not whatever the model claimed.
    response = (
        '{"answer": "Yes.", '
        '"citations": [{"chunk_id": "c1", "source": "attacker-controlled.md"}], '
        '"abstained": false, "confidence": 0.7}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is False
    assert result.citations[0].source == "policy.md"


def test_underlying_client_error_propagates_instead_of_being_swallowed():
    class RaisingChatClient:
        def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
            raise ConnectionError("ollama is not reachable")

    try:
        generate_answer("does the vpn need mfa?", CONTEXT, RaisingChatClient())
    except ConnectionError:
        pass
    else:
        raise AssertionError("expected the transport error to propagate")
