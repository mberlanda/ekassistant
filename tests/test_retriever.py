from ekassistant.index.types import SearchResult
from ekassistant.retrieval.reranker import PassthroughReranker
from ekassistant.retrieval.retriever import retrieve, rewrite_query


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id, source=f"{chunk_id}.md", text=f"text {chunk_id}", score=1.0
    )


class FakeEmbedder:
    def __init__(self):
        self.embedded_queries: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.embedded_queries.append(text)
        return [0.1, 0.2]


class FakeVectorIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results
        self.calls: list[tuple] = []

    def search(self, query_embedding, allowed_groups, top_n):
        self.calls.append((query_embedding, allowed_groups, top_n))
        return self._results


class FakeKeywordIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results
        self.calls: list[tuple] = []

    def search(self, query_text, allowed_groups, top_n):
        self.calls.append((query_text, allowed_groups, top_n))
        return self._results


def test_rewrite_query_normalizes_whitespace():
    assert rewrite_query("  does the   vpn\nneed mfa?  ") == "does the vpn need mfa?"


def test_retrieve_passes_the_callers_real_allowed_groups_to_both_indexes():
    vector_index = FakeVectorIndex([_hit("a")])
    keyword_index = FakeKeywordIndex([_hit("a")])

    retrieve(
        "does the vpn need mfa?",
        ["engineering", "all-staff"],
        FakeEmbedder(),
        vector_index,
        keyword_index,
        PassthroughReranker(),
    )

    assert vector_index.calls[0][1] == ["engineering", "all-staff"]
    assert keyword_index.calls[0][1] == ["engineering", "all-staff"]


def test_retrieve_with_no_groups_still_calls_through_not_bypassed():
    # Fail-closed behavior lives inside the index adapters (see
    # test_vector_index.py / test_keyword_index.py); this only proves the
    # retriever never substitutes or skips passing the real (empty) group
    # set - the fakes here just echo back empty results, matching what a
    # real fail-closed index would do.
    vector_index = FakeVectorIndex([])
    keyword_index = FakeKeywordIndex([])

    result = retrieve(
        "does the vpn need mfa?",
        [],
        FakeEmbedder(),
        vector_index,
        keyword_index,
        PassthroughReranker(),
    )

    assert result == []
    assert vector_index.calls[0][1] == []
    assert keyword_index.calls[0][1] == []


def test_retrieve_fuses_dense_and_keyword_hits_into_context_chunks():
    vector_index = FakeVectorIndex([_hit("a"), _hit("b")])
    keyword_index = FakeKeywordIndex([_hit("b"), _hit("c")])

    result = retrieve(
        "question",
        ["engineering"],
        FakeEmbedder(),
        vector_index,
        keyword_index,
        PassthroughReranker(),
    )

    chunk_ids = {c.chunk_id for c in result}
    assert chunk_ids == {"a", "b", "c"}
    # "b" appeared in both lists, should be ranked first by RRF.
    assert result[0].chunk_id == "b"


def test_retrieve_respects_final_k_after_reranking():
    vector_index = FakeVectorIndex([_hit("a"), _hit("b"), _hit("c")])
    keyword_index = FakeKeywordIndex([])

    result = retrieve(
        "question",
        ["engineering"],
        FakeEmbedder(),
        vector_index,
        keyword_index,
        PassthroughReranker(),
        final_k=2,
    )

    assert len(result) == 2


def test_retrieve_with_blank_question_returns_nothing_without_calling_embedder():
    embedder = FakeEmbedder()

    result = retrieve(
        "   ",
        ["engineering"],
        embedder,
        FakeVectorIndex([]),
        FakeKeywordIndex([]),
        PassthroughReranker(),
    )

    assert result == []
    assert embedder.embedded_queries == []


def test_retrieve_returns_context_chunks_not_raw_search_results():
    vector_index = FakeVectorIndex([_hit("a")])
    keyword_index = FakeKeywordIndex([])

    result = retrieve(
        "question",
        ["engineering"],
        FakeEmbedder(),
        vector_index,
        keyword_index,
        PassthroughReranker(),
    )

    assert result[0].chunk_id == "a"
    assert result[0].source == "a.md"
    assert result[0].text == "text a"
    assert not hasattr(result[0], "score")  # ContextChunk carries no score
