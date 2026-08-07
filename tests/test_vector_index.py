"""Integration tests against a live Qdrant instance (`make up`).

Skipped automatically if Qdrant isn't reachable, so `pytest` still passes
on a machine that hasn't started docker-compose. Uses a throwaway,
uniquely-named collection per test run so this never touches the real
`ekassistant_chunks` collection or leaves state behind.
"""

import uuid

import pytest

from ekassistant.config.settings import Settings
from ekassistant.index.types import IndexedChunk

qdrant_client_module = pytest.importorskip("qdrant_client")


def _qdrant_is_reachable(url: str) -> bool:
    try:
        qdrant_client_module.QdrantClient(url=url).get_collections()
    except Exception:
        return False
    return True


ENGINEERING_CHUNK = IndexedChunk(
    chunk_id="c1",
    source="runbook.md",
    text="deploy pipeline runbook",
    allowed_groups=["engineering"],
)
FINANCE_CHUNK = IndexedChunk(
    chunk_id="c2",
    source="expenses.md",
    text="expense report policy",
    allowed_groups=["finance"],
)
MULTI_GROUP_CHUNK = IndexedChunk(
    chunk_id="c3",
    source="launch-plan.md",
    text="product launch rollout plan",
    allowed_groups=["engineering", "product"],
)
# Both chunks embed near-identically so ACL filtering, not vector distance,
# is what determines whether a result comes back.
EMBEDDING = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def vector_index():
    from ekassistant.index.vector_index import QdrantVectorIndex

    settings = Settings(
        qdrant_collection=f"test_{uuid.uuid4().hex}",
        embedding_dimensions=len(EMBEDDING),
    )
    if not _qdrant_is_reachable(settings.qdrant_url):
        pytest.skip("Qdrant is not reachable at " + settings.qdrant_url + " (run `make up`)")

    index = QdrantVectorIndex(settings)
    index.ensure_collection()
    yield index

    cleanup_client = qdrant_client_module.QdrantClient(url=settings.qdrant_url)
    cleanup_client.delete_collection(settings.qdrant_collection)


def test_search_finds_matching_chunk_visible_to_the_group(vector_index):
    vector_index.upsert(ENGINEERING_CHUNK, EMBEDDING)

    results = vector_index.search(EMBEDDING, allowed_groups=["engineering"], top_n=10)

    assert [r.chunk_id for r in results] == ["c1"]


def test_search_excludes_chunk_outside_the_caller_groups(vector_index):
    vector_index.upsert(FINANCE_CHUNK, EMBEDDING)

    results = vector_index.search(EMBEDDING, allowed_groups=["engineering"], top_n=10)

    assert results == []


def test_search_with_no_groups_returns_nothing_without_querying(vector_index):
    vector_index.upsert(FINANCE_CHUNK, EMBEDDING)

    results = vector_index.search(EMBEDDING, allowed_groups=[], top_n=10)

    assert results == []


def test_upsert_is_idempotent_not_duplicating_on_re_ingest(vector_index):
    vector_index.upsert(ENGINEERING_CHUNK, EMBEDDING)
    vector_index.upsert(ENGINEERING_CHUNK, EMBEDDING)

    results = vector_index.search(EMBEDDING, allowed_groups=["engineering"], top_n=10)

    assert len(results) == 1


def test_delete_propagates_so_the_chunk_no_longer_matches(vector_index):
    vector_index.upsert(ENGINEERING_CHUNK, EMBEDDING)
    vector_index.delete(ENGINEERING_CHUNK.chunk_id)

    results = vector_index.search(EMBEDDING, allowed_groups=["engineering"], top_n=10)

    assert results == []


def test_partial_group_overlap_is_enough_to_see_a_multi_group_chunk(vector_index):
    # Caller belongs to {marketing, product}; the chunk is allowed to
    # {engineering, product}. Only "product" overlaps - that must be
    # enough (OR semantics across groups via MatchAny), not require every
    # group to match.
    vector_index.upsert(MULTI_GROUP_CHUNK, EMBEDDING)

    results = vector_index.search(EMBEDDING, allowed_groups=["marketing", "product"], top_n=10)

    assert [r.chunk_id for r in results] == [MULTI_GROUP_CHUNK.chunk_id]


def test_no_group_overlap_at_all_excludes_a_multi_group_chunk(vector_index):
    vector_index.upsert(MULTI_GROUP_CHUNK, EMBEDDING)

    results = vector_index.search(EMBEDDING, allowed_groups=["marketing", "sales"], top_n=10)

    assert results == []


def test_ensure_collection_is_idempotent(vector_index):
    vector_index.ensure_collection()
    vector_index.ensure_collection()


def test_delete_of_never_upserted_chunk_does_not_raise(vector_index):
    vector_index.delete("does-not-exist")
