"""Governance regression tests: ACL propagation on delete/revoke.

See docs/design/governance.md#responsibilities item 2 and docs/roadmap.md
item 9. Runs the real ingest pipeline (ekassistant.ingest.pipeline.run_ingest)
against a REAL Qdrant instance (`make up`) and a real SQLite file, not
mocks - the property under test is specifically "does a delete/ACL-revoke
reach both concrete index implementations," which a mocked index can't
prove either way. Skipped automatically if Qdrant isn't reachable, same
as tests/test_vector_index.py.
"""

import uuid

import pytest

from ekassistant.config.settings import Settings
from ekassistant.ingest.connectors.filesystem import Document
from ekassistant.ingest.pipeline import run_ingest

qdrant_client_module = pytest.importorskip("qdrant_client")

EMBEDDING = [0.1, 0.2, 0.3, 0.4]


def _qdrant_is_reachable(url: str) -> bool:
    try:
        qdrant_client_module.QdrantClient(url=url).get_collections()
    except Exception:
        return False
    return True


class FakeConnector:
    def __init__(self, documents: list[Document]):
        self._documents = documents

    def load_documents(self) -> list[Document]:
        return self._documents


class ConstantEmbedder:
    """A real embedding call isn't the point of this test - only that a
    delete/ACL change reaches both indexes - so a fixed vector stands in
    for Ollama.
    """

    def embed(self, text: str) -> list[float]:
        return EMBEDDING


@pytest.fixture
def indexes(tmp_path):
    from ekassistant.index.keyword_index import SqliteKeywordIndex
    from ekassistant.index.vector_index import QdrantVectorIndex

    settings = Settings(
        qdrant_collection=f"test_governance_{uuid.uuid4().hex}",
        embedding_dimensions=len(EMBEDDING),
        keyword_index_path=tmp_path / "keyword_index.sqlite3",
    )
    if not _qdrant_is_reachable(settings.qdrant_url):
        pytest.skip("Qdrant is not reachable at " + settings.qdrant_url + " (run `make up`)")

    vector_index = QdrantVectorIndex(settings)
    vector_index.ensure_collection()
    keyword_index = SqliteKeywordIndex(settings)

    yield vector_index, keyword_index

    cleanup_client = qdrant_client_module.QdrantClient(url=settings.qdrant_url)
    cleanup_client.delete_collection(settings.qdrant_collection)
    keyword_index.close()


def _visible_to(vector_index, keyword_index, group: str) -> bool:
    vector_hit = bool(vector_index.search(EMBEDDING, allowed_groups=[group], top_n=10))
    keyword_hit = bool(
        keyword_index.search("policy content", allowed_groups=[group], top_n=10)
    )
    return vector_hit or keyword_hit


def test_revoking_a_groups_access_removes_it_from_both_indexes(indexes):
    vector_index, keyword_index = indexes
    embedder = ConstantEmbedder()

    run_ingest(
        FakeConnector(
            [Document("policy.md", "policy.md", "Some policy content.", ["finance"])]
        ),
        embedder,
        vector_index,
        keyword_index,
    )
    assert _visible_to(vector_index, keyword_index, "finance") is True
    assert _visible_to(vector_index, keyword_index, "engineering") is False

    # Access revoked from finance, granted to engineering instead - same
    # source, re-ingested with a different ACL.
    run_ingest(
        FakeConnector(
            [Document("policy.md", "policy.md", "Some policy content.", ["engineering"])]
        ),
        embedder,
        vector_index,
        keyword_index,
    )

    assert _visible_to(vector_index, keyword_index, "finance") is False
    assert _visible_to(vector_index, keyword_index, "engineering") is True


def test_removing_a_document_entirely_deletes_it_from_both_indexes(indexes):
    vector_index, keyword_index = indexes
    embedder = ConstantEmbedder()

    run_ingest(
        FakeConnector(
            [Document("secret.md", "secret.md", "Sensitive policy content.", ["finance"])]
        ),
        embedder,
        vector_index,
        keyword_index,
    )
    assert _visible_to(vector_index, keyword_index, "finance") is True

    # Document removed from the connector's source entirely (deleted from
    # the manifest, or the whole source decommissioned).
    run_ingest(FakeConnector([]), embedder, vector_index, keyword_index)

    assert _visible_to(vector_index, keyword_index, "finance") is False
    assert vector_index.all_sources() == set()
    assert keyword_index.all_sources() == set()


def test_a_second_unrelated_document_survives_the_first_ones_deletion(indexes):
    # Guards against an over-broad delete (e.g. deleting by source prefix
    # or wiping the whole collection instead of just the removed source's
    # chunks) - a real risk given delete-propagation now runs on every
    # ingest call.
    vector_index, keyword_index = indexes
    embedder = ConstantEmbedder()

    run_ingest(
        FakeConnector(
            [
                Document("secret.md", "secret.md", "Sensitive policy content.", ["finance"]),
                Document("public.md", "public.md", "Public policy content.", ["all-staff"]),
            ]
        ),
        embedder,
        vector_index,
        keyword_index,
    )

    run_ingest(
        FakeConnector(
            [Document("public.md", "public.md", "Public policy content.", ["all-staff"])]
        ),
        embedder,
        vector_index,
        keyword_index,
    )

    assert _visible_to(vector_index, keyword_index, "finance") is False
    assert _visible_to(vector_index, keyword_index, "all-staff") is True
    assert vector_index.all_sources() == {"public.md"}
    assert keyword_index.all_sources() == {"public.md"}
