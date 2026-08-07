from ekassistant.models.context import ContextChunk
from ekassistant.models.generation import generate_answer

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


def test_citation_outside_context_downgrades_to_abstain():
    response = (
        '{"answer": "Yes.", '
        '"citations": [{"chunk_id": "does-not-exist", "source": "policy.md"}], '
        '"abstained": false}'
    )
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True


def test_no_citations_but_not_abstained_downgrades_to_abstain():
    response = '{"answer": "Yes.", "citations": [], "abstained": false}'
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True


def test_malformed_json_downgrades_to_abstain():
    client = FakeChatClient(response="not valid json at all")
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True


def test_model_reported_abstain_is_normalized():
    response = '{"answer": "irrelevant text", "citations": [], "abstained": true}'
    client = FakeChatClient(response)
    result = generate_answer("does the vpn need mfa?", CONTEXT, client)
    assert result.abstained is True
    assert result.answer == ""
    assert result.citations == []
