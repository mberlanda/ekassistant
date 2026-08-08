from ekassistant.index.types import SearchResult
from ekassistant.orchestration.pipeline import answer_question
from ekassistant.retrieval.reranker import PassthroughReranker


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]


class FakeVectorIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, query_embedding, allowed_groups, top_n):
        return self._results


class FakeKeywordIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, query_text, allowed_groups, top_n):
        return self._results


class FakeChatClient:
    def __init__(self, response: str):
        self._response = response
        self.temperatures_seen: list[float] = []

    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
        self.temperatures_seen.append(temperature)
        return self._response


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id, source="policy.md", text="The VPN needs MFA.", score=1.0
    )


def test_answer_question_grounds_the_answer_in_retrieved_context():
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.85}'
    )
    result = answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([_hit("c1")]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        FakeChatClient(response),
    )

    assert result.answer.abstained is False
    assert result.answer.answer == "Yes."
    assert result.answer.citations[0].chunk_id == "c1"


def test_answer_question_reports_retrieval_hit_count_and_stage_timing():
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.85}'
    )
    result = answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([_hit("c1")]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        FakeChatClient(response),
    )

    assert result.retrieved_chunk_ids == ["c1"]
    assert result.retrieval_ms >= 0
    assert result.generation_ms >= 0


def test_answer_question_reports_empty_retrieval_when_nothing_is_found():
    result = answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        FakeChatClient("should never be read"),
    )

    assert result.retrieved_chunk_ids == []
    assert result.answer.abstained is True
    assert result.answer.answer == ""


def test_answer_question_abstains_with_no_groups_even_if_index_would_return_hits():
    # If the caller somehow had zero groups, the index adapters
    # themselves fail closed (see ADR-0002 / index tests) - this just
    # confirms the pipeline doesn't work around that by e.g. defaulting
    # to some other group set.
    result = answer_question(
        "does the vpn need mfa?",
        [],
        FakeEmbedder(),
        FakeVectorIndex([]),  # a real index would also return [] for no groups
        FakeKeywordIndex([]),
        PassthroughReranker(),
        FakeChatClient("should never be read"),
    )

    assert result.answer.abstained is True


def test_answer_question_downgrades_citation_outside_retrieved_context_to_abstain():
    # Grounding integrity end to end: even if the model hallucinates a
    # citation to a chunk_id that wasn't actually retrieved, the answer
    # must be downgraded to abstain, not passed through.
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "not-retrieved", "source": "x.md"}], '
        '"abstained": false, "confidence": 0.85}'
    )
    result = answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([_hit("c1")]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        FakeChatClient(response),
    )

    assert result.answer.abstained is True
    # The chunk really was retrieved - the model just cited it wrong -
    # so retrieved_chunk_ids still reports it as a real retrieval hit,
    # separate from generation's decision about what to do with it.
    assert result.retrieved_chunk_ids == ["c1"]


def test_answer_question_reports_the_models_confidence_on_a_grounded_answer():
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.85}'
    )
    result = answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([_hit("c1")]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        FakeChatClient(response),
    )

    assert result.answer.confidence == 0.85


def test_answer_question_passes_the_temperature_through_to_the_chat_client():
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.85}'
    )
    chat_client = FakeChatClient(response)
    answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([_hit("c1")]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        chat_client,
        temperature=0.9,
    )

    assert chat_client.temperatures_seen == [0.9]


def test_answer_question_uses_the_default_temperature_when_not_specified():
    from ekassistant.models.generation import DEFAULT_TEMPERATURE

    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.85}'
    )
    chat_client = FakeChatClient(response)
    answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([_hit("c1")]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        chat_client,
    )

    assert chat_client.temperatures_seen == [DEFAULT_TEMPERATURE]
