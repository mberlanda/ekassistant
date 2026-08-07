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


def test_delete_propagates_so_the_chunk_no_longer_matches(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)
    index.delete(ENGINEERING_CHUNK.chunk_id)

    results = index.search("deploy pipeline", allowed_groups=["engineering"], top_n=10)

    assert results == []


def test_search_score_is_higher_is_better(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)

    results = index.search("deploy pipeline", allowed_groups=["engineering"], top_n=10)

    assert results[0].score > 0


def test_query_containing_fts5_operator_syntax_does_not_raise(tmp_path):
    index = _make_index(tmp_path)
    index.upsert(ENGINEERING_CHUNK)

    # "OR", "NOT", "*", unbalanced quotes are all meaningful to FTS5's own
    # query syntax - a literal user question containing them must not
    # blow up the search or be reinterpreted as query operators.
    results = index.search('deploy OR NOT "pipeline* -- unbalanced" quote', ["engineering"], 10)

    assert isinstance(results, list)
