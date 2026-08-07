from ekassistant.config.settings import Settings
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.types import IndexedChunk

ENGINEERING_CHUNK = IndexedChunk(
    chunk_id="c1",
    source="runbook.md",
    text="Restart the deploy pipeline by re-running the failed stage.",
    allowed_groups=["engineering"],
)
FINANCE_CHUNK = IndexedChunk(
    chunk_id="c2",
    source="expenses.md",
    text="Submit expense reports within thirty days of purchase.",
    allowed_groups=["finance"],
)
MULTI_GROUP_CHUNK = IndexedChunk(
    chunk_id="c3",
    source="launch-plan.md",
    text="Coordinate the product launch rollout across regions.",
    allowed_groups=["engineering", "product"],
)


def _make_index(tmp_path) -> SqliteKeywordIndex:
    settings = Settings(keyword_index_path=tmp_path / "keyword_index.sqlite3")
    return SqliteKeywordIndex(settings)


def test_search_finds_matching_chunk_visible_to_the_group(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)

    results = index.search("deploy pipeline", allowed_groups=["engineering"], top_n=10)

    assert [r.chunk_id for r in results] == ["c1"]


def test_search_excludes_chunk_outside_the_caller_groups(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(FINANCE_CHUNK)

    results = index.search("expense reports", allowed_groups=["engineering"], top_n=10)

    assert results == []


def test_search_with_no_groups_returns_nothing_without_querying(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(FINANCE_CHUNK)

    results = index.search("expense reports", allowed_groups=[], top_n=10)

    assert results == []


def test_upsert_is_idempotent_not_duplicating_on_re_ingest(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)
    index.upsert(ENGINEERING_CHUNK)

    results = index.search("deploy pipeline", allowed_groups=["engineering"], top_n=10)

    assert len(results) == 1


def test_upsert_of_existing_chunk_replaces_text_and_group_membership(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)

    updated = IndexedChunk(
        chunk_id=ENGINEERING_CHUNK.chunk_id,
        source=ENGINEERING_CHUNK.source,
        text="Escalate to the on-call rotation via the incident tool.",
        allowed_groups=["finance"],
    )
    index.upsert(updated)

    assert index.search("deploy pipeline", allowed_groups=["engineering"], top_n=10) == []
    finance_results = index.search("on-call rotation", allowed_groups=["finance"], top_n=10)
    assert [r.chunk_id for r in finance_results] == [ENGINEERING_CHUNK.chunk_id]


def test_delete_propagates_so_the_chunk_no_longer_matches(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)
    index.delete(ENGINEERING_CHUNK.chunk_id)

    results = index.search("deploy pipeline", allowed_groups=["engineering"], top_n=10)

    assert results == []


def test_partial_group_overlap_is_enough_to_see_a_multi_group_chunk(tmp_path):
    # Caller belongs to {marketing, product}; the chunk is allowed to
    # {engineering, product}. Only "product" overlaps - that must be
    # enough (OR semantics across groups), not require every group to
    # match (that would be a silent, severe over-restriction bug).
    index = _make_index(tmp_path)
    index.upsert(MULTI_GROUP_CHUNK)

    results = index.search(
        "product launch", allowed_groups=["marketing", "product"], top_n=10
    )

    assert [r.chunk_id for r in results] == [MULTI_GROUP_CHUNK.chunk_id]


def test_no_group_overlap_at_all_excludes_a_multi_group_chunk(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(MULTI_GROUP_CHUNK)

    results = index.search("product launch", allowed_groups=["marketing", "sales"], top_n=10)

    assert results == []


def test_search_score_is_higher_is_better(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)

    results = index.search("deploy pipeline", allowed_groups=["engineering"], top_n=10)

    assert results[0].score > 0


def test_chunk_ids_for_source_returns_only_that_sources_chunks(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)
    index.upsert(FINANCE_CHUNK)

    assert index.chunk_ids_for_source("runbook.md") == {ENGINEERING_CHUNK.chunk_id}
    assert index.chunk_ids_for_source("no-such-source.md") == set()


def test_all_sources_returns_the_distinct_set_of_indexed_sources(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)
    index.upsert(FINANCE_CHUNK)

    assert index.all_sources() == {"runbook.md", "expenses.md"}


def test_query_containing_fts5_operator_syntax_does_not_raise(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)

    # "OR", "NOT", "*", unbalanced quotes are all meaningful to FTS5's own
    # query syntax - a literal user question containing them must not
    # blow up the search or be reinterpreted as query operators.
    results = index.search('deploy OR NOT "pipeline* -- unbalanced" quote', ["engineering"], 10)

    assert isinstance(results, list)
