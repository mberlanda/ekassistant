"""Reranking interface. See docs/design/retrieval.md.

A real cross-encoder reranker re-scores the fused candidates jointly
against the query text, which is more accurate than the rank-position
heuristic RRF uses but requires its own model download. Deferred until
one is pulled - PassthroughReranker keeps the pipeline runnable end to
end in the meantime, matching ADR-0004's pattern for the LLM/embedding
models.
"""

from typing import Protocol

from ekassistant.index.types import SearchResult


class Reranker(Protocol):
    def rerank(
        self, query: str, candidates: list[SearchResult], top_k: int
    ) -> list[SearchResult]: ...


class PassthroughReranker:
    """Keeps the RRF-fused order, just truncates to top_k."""

    def rerank(
        self, query: str, candidates: list[SearchResult], top_k: int
    ) -> list[SearchResult]:
        # max(top_k, 0): a plain `candidates[:top_k]` would silently return
        # almost the whole list for a negative top_k (Python slice
        # semantics), rather than the "give me nothing" a caller passing
        # a negative count almost certainly meant.
        return candidates[: max(top_k, 0)]
