"""ACL-filtered hybrid retrieval: dense + BM25 -> RRF fuse -> rerank -> context.

See docs/design/retrieval.md. Mirrors the pipeline stages in that doc:
rewrite -> ACL-filtered hybrid search -> RRF fusion -> rerank -> assemble
context. ACL enforcement itself lives inside vector_index.search() and
keyword_index.search() (see ADR-0002) - this function's only
responsibility for it is to always pass the caller's real allowed_groups
through, never to skip or default it.
"""

from typing import Protocol

from ekassistant.index.types import SearchResult
from ekassistant.models.context import ContextChunk
from ekassistant.retrieval.reranker import Reranker
from ekassistant.retrieval.rrf import reciprocal_rank_fusion


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class VectorSearcher(Protocol):
    def search(
        self, query_embedding: list[float], allowed_groups: list[str], top_n: int
    ) -> list[SearchResult]: ...


class KeywordSearcher(Protocol):
    def search(
        self, query_text: str, allowed_groups: list[str], top_n: int
    ) -> list[SearchResult]: ...


def rewrite_query(question: str) -> str:
    """V1 is close to a no-op (whitespace normalization only) - see
    docs/design/retrieval.md#tradeoffs: real query rewriting/decomposition
    shades into the agentic multi-hop retrieval the brief defers.
    """
    return " ".join(question.split())


def retrieve(
    question: str,
    allowed_groups: list[str],
    embed_client: Embedder,
    vector_index: VectorSearcher,
    keyword_index: KeywordSearcher,
    reranker: Reranker,
    candidate_top_n: int = 20,
    final_k: int = 5,
) -> list[ContextChunk]:
    query = rewrite_query(question)
    if not query:
        return []

    query_embedding = embed_client.embed(query)
    dense_hits = vector_index.search(query_embedding, allowed_groups, candidate_top_n)
    keyword_hits = keyword_index.search(query, allowed_groups, candidate_top_n)

    fused = reciprocal_rank_fusion([dense_hits, keyword_hits])
    reranked = reranker.rerank(query, fused, final_k)

    return [
        ContextChunk(chunk_id=result.chunk_id, source=result.source, text=result.text)
        for result in reranked
    ]
