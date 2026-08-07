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
    if k < 0:
        # rank starts at 1 and only increases, so k=0 is safe (equivalent
        # to plain, unsmoothed reciprocal-rank scoring: 1/rank) - but a
        # negative k is a real hazard, not just a crash risk: for a list
        # shorter than |k|, every rank stays below |k| and (k + rank)
        # never hits zero, so instead of a ZeroDivisionError this would
        # silently produce NEGATIVE score contributions for being ranked
        # highly, corrupting the fused order without ever raising.
        raise ValueError(f"k must be >= 0, got {k}")

    scores: dict[str, float] = {}
    # setdefault: the first list a chunk_id is seen in (result_lists[0],
    # conventionally the dense/vector hits - see retriever.py's call site)
    # supplies its displayed source/text if the two stores ever disagree
    # for the same chunk_id. They shouldn't (see ADR-0005's consistency
    # discussion) - this only matters if ingest.pipeline's documented
    # stale-chunk gap has left one store's copy of a chunk out of date
    # relative to the other's.
    representative: dict[str, SearchResult] = {}
    for results in result_lists:
        for rank, result in enumerate(results, start=1):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (k + rank)
            representative.setdefault(result.chunk_id, result)

    # Ties (equal fused score) resolve by dict insertion order, i.e.
    # whichever chunk_id was first encountered scanning result_lists in
    # order - deterministic, not random, but also not a meaningful
    # relevance signal; acceptable since exact ties are rare in practice.
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
