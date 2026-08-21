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
        self.user_prompts_seen: list[str] = []

    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
        self.temperatures_seen.append(temperature)
        self.user_prompts_seen.append(user)
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
        '"citations": [{"index": 1}], "abstained": false, '
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
        '{"answer": "Yes.", "citations": [{"index": 1}], '
        '"abstained": false, "confidence": 0.5}'
    )
    client = FakeChatClient(response)
    generate_answer("does the vpn need mfa?", CONTEXT, client, temperature=0.7)
    assert client.temperatures_seen == [0.7]


def test_citation_outside_context_downgrades_to_abstain():
    # CONTEXT has exactly one chunk, so index 99 is the index-based
    # equivalent of the old "invented chunk_id" case.
    response = (
        '{"answer": "Yes.", '
        '"citations": [{"index": 99}], '
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
        '{"answer": "Yes.", "citations": [{"index": 1}], '
        '"abstained": false}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is False
    assert result.confidence is None


def test_an_out_of_range_confidence_downgrades_to_abstain():
    response = (
        '{"answer": "Yes.", "citations": [{"index": 1}], '
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
        '{"answer": "", "citations": [{"index": 1}], '
        '"abstained": false, "confidence": 0.6}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_BLANK_ANSWER
    assert result.confidence is None


def test_citation_source_is_rebuilt_from_context_not_trusted_from_the_model():
    # The model is no longer asked for chunk_id/source at all, but a model
    # can still volunteer extra fields. They must be ignored entirely: the
    # cited index is resolved against CONTEXT, so an injected or fabricated
    # label cannot reach the caller.
    response = (
        '{"answer": "Yes.", '
        '"citations": [{"index": 1, "chunk_id": "spoofed", '
        '"source": "attacker-controlled.md"}], '
        '"abstained": false, "confidence": 0.7}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is False
    assert result.citations[0].source == "policy.md"
    assert result.citations[0].chunk_id == "c1"


def test_citation_index_below_one_downgrades_to_abstain():
    # ModelCitation.index is ge=1, so a 0-based or negative index fails
    # field validation rather than silently resolving to the last chunk
    # via Python's negative indexing.
    response = '{"answer": "Yes.", "citations": [{"index": 0}], "abstained": false}'
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.reason == REASON_MALFORMED_RESPONSE


def test_composite_chunk_ids_survive_the_round_trip():
    # Regression test for the contract bug this indirection exists to fix:
    # a crawler-produced chunk_id is itself composite (`<url>#<n>`), which
    # invited models to split it across the old chunk_id/source fields and
    # get their otherwise-correct answers thrown away. The model now only
    # ever sees, and returns, a position - so the shape of the real
    # chunk_id cannot affect citation validity at all.
    context = [
        ContextChunk(
            chunk_id="https://example.com/p/some-post#7",
            source="https://example.com/p/some-post",
            text="Quantik is a board game.",
        ),
        ContextChunk(
            chunk_id="https://example.com/p/other-post#12",
            source="https://example.com/p/other-post",
            text="Unrelated.",
        ),
    ]
    response = '{"answer": "It is a board game.", "citations": [{"index": 1}], "abstained": false}'
    result = generate_answer("what is quantik?", context, FakeChatClient(response))
    assert result.abstained is False
    assert result.citations[0].chunk_id == "https://example.com/p/some-post#7"
    assert result.citations[0].source == "https://example.com/p/some-post"


def test_context_is_rendered_with_positional_indexes_not_chunk_ids():
    # The prompt the model actually sees must label chunks [1], [2], ...
    # If real chunk_ids leaked back into the rendering, the model would be
    # invited to cite them again and the indirection would be pointless.
    context = [
        ContextChunk(chunk_id="doc.md#41", source="doc.md", text="first"),
        ContextChunk(chunk_id="doc.md#99", source="doc.md", text="second"),
    ]
    client = FakeChatClient('{"answer": "x", "citations": [{"index": 2}], "abstained": false}')
    result = generate_answer("q", context, client)
    rendered = client.user_prompts_seen[0]
    assert "[1]" in rendered and "[2]" in rendered
    assert "doc.md#41" not in rendered
    assert "doc.md#99" not in rendered
    # ...and index 2 resolves to the second chunk, not the first.
    assert result.citations[0].chunk_id == "doc.md#99"


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
