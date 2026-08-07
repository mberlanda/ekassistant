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
