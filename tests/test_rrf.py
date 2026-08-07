import pytest

from ekassistant.index.types import SearchResult
from ekassistant.retrieval.rrf import reciprocal_rank_fusion


def _hit(chunk_id: str, score: float = 1.0) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id, source=f"{chunk_id}.md", text=f"text {chunk_id}", score=score
    )


def test_chunk_present_in_both_lists_outranks_one_present_in_only_one():
    dense = [_hit("a"), _hit("b")]
    keyword = [_hit("b"), _hit("c")]

    fused = reciprocal_rank_fusion([dense, keyword])

    # "b" is #2 in dense and #1 in keyword - present in both, should be
    # ranked ahead of "a" and "c", each present in only one list.
    assert fused[0].chunk_id == "b"


def test_top_rank_in_a_single_list_beats_low_rank_in_both():
    dense = [_hit("a")]
    keyword = [_hit("z1"), _hit("z2"), _hit("z3"), _hit("a")]

    fused = reciprocal_rank_fusion([dense, keyword])

    # "a" is #1 in dense and #4 in keyword: 1/61 + 1/64 vs "z1" #1 in
    # keyword only: 1/61. "a" should still win since it appears in both.
    assert fused[0].chunk_id == "a"


def test_no_duplicate_entries_for_a_chunk_in_both_lists():
    dense = [_hit("a")]
    keyword = [_hit("a")]

    fused = reciprocal_rank_fusion([dense, keyword])

    assert [r.chunk_id for r in fused] == ["a"]


def test_empty_lists_produce_no_results():
    assert reciprocal_rank_fusion([[], []]) == []


def test_result_retains_its_source_and_text():
    dense = [SearchResult(chunk_id="a", source="policy.md", text="the actual content", score=0.9)]

    fused = reciprocal_rank_fusion([dense, []])

    assert fused[0].source == "policy.md"
    assert fused[0].text == "the actual content"


def test_different_k_values_both_produce_valid_fused_results():
    dense = [_hit("a"), _hit("b")]
    keyword = [_hit("b"), _hit("a")]

    fused_default = reciprocal_rank_fusion([dense, keyword])
    fused_small_k = reciprocal_rank_fusion([dense, keyword], k=1)

    # Symmetric ranks (a:1,2 vs b:2,1) tie regardless of k - just confirm
    # both a smaller and the default k don't raise and produce both chunks.
    assert {r.chunk_id for r in fused_default} == {"a", "b"}
    assert {r.chunk_id for r in fused_small_k} == {"a", "b"}


def test_k_zero_is_allowed_plain_reciprocal_rank():
    fused = reciprocal_rank_fusion([[_hit("a")], []], k=0)

    assert fused[0].chunk_id == "a"
    assert fused[0].score == 1.0  # 1/(0+1)


def test_negative_k_is_rejected_rather_than_silently_corrupting_scores():
    with pytest.raises(ValueError, match="k must be >= 0"):
        reciprocal_rank_fusion([[_hit("a")], []], k=-1)
