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

    def chat_json(self, system: str, user: str, json_schema: dict) -> str:
        return self._response


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, source="policy.md", text="The VPN needs MFA.", score=1.0)


def test_answer_question_grounds_the_answer_in_retrieved_context():
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false}'
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

    assert result.abstained is False
    assert result.answer == "Yes."
    assert result.citations[0].chunk_id == "c1"


def test_answer_question_abstains_when_nothing_is_retrieved():
    result = answer_question(
        "does the vpn need mfa?",
        ["engineering"],
        FakeEmbedder(),
        FakeVectorIndex([]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
        FakeChatClient("should never be read"),
    )

    assert result.abstained is True
    assert result.answer == ""


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

    assert result.abstained is True


def test_answer_question_downgrades_citation_outside_retrieved_context_to_abstain():
    # Grounding integrity end to end: even if the model hallucinates a
    # citation to a chunk_id that wasn't actually retrieved, the answer
    # must be downgraded to abstain, not passed through.
    response = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "not-retrieved", "source": "x.md"}], '
        '"abstained": false}'
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

    assert result.abstained is True
