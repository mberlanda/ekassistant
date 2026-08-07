from ekassistant.index.types import SearchResult
from ekassistant.retrieval.reranker import PassthroughReranker


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, source=f"{chunk_id}.md", text="text", score=1.0)


def test_passthrough_reranker_keeps_order():
    candidates = [_hit("a"), _hit("b"), _hit("c")]

    result = PassthroughReranker().rerank("irrelevant query", candidates, top_k=10)

    assert [r.chunk_id for r in result] == ["a", "b", "c"]


def test_passthrough_reranker_truncates_to_top_k():
    candidates = [_hit("a"), _hit("b"), _hit("c")]

    result = PassthroughReranker().rerank("irrelevant query", candidates, top_k=2)

    assert [r.chunk_id for r in result] == ["a", "b"]


def test_passthrough_reranker_negative_top_k_returns_nothing_not_almost_everything():
    # A bare candidates[:top_k] would silently return candidates[:-1] (all
    # but the last item) for top_k=-1 via Python slice semantics - the
    # opposite of what a negative count should mean.
    candidates = [_hit("a"), _hit("b"), _hit("c")]

    result = PassthroughReranker().rerank("irrelevant query", candidates, top_k=-1)

    assert result == []


def test_passthrough_reranker_zero_top_k_returns_nothing():
    candidates = [_hit("a"), _hit("b"), _hit("c")]

    result = PassthroughReranker().rerank("irrelevant query", candidates, top_k=0)

    assert result == []
