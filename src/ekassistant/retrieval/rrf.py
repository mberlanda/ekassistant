"""Reciprocal Rank Fusion. See ADR-0003.

rrf_score(doc) = sum, over every ranked list containing doc, of
1 / (k + rank_in_that_list). Scale-free (uses rank position only, never
raw score), so no normalization is needed between dense/keyword scores
that live on incomparable scales.
"""

from ekassistant.index.types import SearchResult

_DEFAULT_K = 60  # the standard default from the original RRF paper


def reciprocal_rank_fusion(
    result_lists: list[list[SearchResult]], k: int = _DEFAULT_K
) -> list[SearchResult]:
    scores: dict[str, float] = {}
    representative: dict[str, SearchResult] = {}
    for results in result_lists:
        for rank, result in enumerate(results, start=1):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (k + rank)
            representative.setdefault(result.chunk_id, result)

    ranked_ids = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
    return [
        SearchResult(
            chunk_id=chunk_id,
            source=representative[chunk_id].source,
            text=representative[chunk_id].text,
            score=scores[chunk_id],
        )
        for chunk_id in ranked_ids
    ]
