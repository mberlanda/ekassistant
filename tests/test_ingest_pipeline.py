from ekassistant.index.types import IndexedChunk
from ekassistant.ingest.connectors.composite import CompositeConnector
from ekassistant.ingest.connectors.filesystem import Document
from ekassistant.ingest.pipeline import run_ingest


class FakeConnector:
    def __init__(self, documents: list[Document]):
        self._documents = documents

    def load_documents(self) -> list[Document]:
        return self._documents


class FakeEmbedder:
    def __init__(self):
        self.embedded_texts: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.embedded_texts.append(text)
        return [float(len(text))]


class FakeIndex:
    """Models a real index adapter's keyed upsert/delete/lookup semantics
    (unlike an append-only log), so re-running ingest against it behaves
    like a real index would - both fakes below share this behavior since
    run_ingest treats the vector and keyword index identically for the
    diff/delete bookkeeping.
    """

    def __init__(self):
        self.by_chunk_id: dict[str, IndexedChunk] = {}

    def chunk_ids_for_source(self, source: str) -> set[str]:
        return {cid for cid, chunk in self.by_chunk_id.items() if chunk.source == source}

    def all_sources(self) -> set[str]:
        return {chunk.source for chunk in self.by_chunk_id.values()}

    def delete(self, chunk_id: str) -> None:
        self.by_chunk_id.pop(chunk_id, None)


class FakeVectorIndex(FakeIndex):
    def __init__(self):
        super().__init__()
        self.upserted: list[tuple[IndexedChunk, list[float]]] = []

    def upsert(self, chunk: IndexedChunk, embedding: list[float]) -> None:
        self.upserted.append((chunk, embedding))
        self.by_chunk_id[chunk.chunk_id] = chunk


class FakeKeywordIndex(FakeIndex):
    def __init__(self):
        super().__init__()
        self.upserted: list[IndexedChunk] = []

    def upsert(self, chunk: IndexedChunk) -> None:
        self.upserted.append(chunk)
        self.by_chunk_id[chunk.chunk_id] = chunk


def test_run_ingest_chunks_embeds_and_writes_to_both_indexes():
    connector = FakeConnector(
        [Document(doc_id="a.md", source="a.md", text="Some content here.", allowed_groups=["eng"])]
    )
    embedder = FakeEmbedder()
    vector_index = FakeVectorIndex()
    keyword_index = FakeKeywordIndex()

    chunk_count = run_ingest(connector, embedder, vector_index, keyword_index)

    assert chunk_count == 1
    assert len(vector_index.upserted) == 1
    assert len(keyword_index.upserted) == 1
    vector_chunk, embedding = vector_index.upserted[0]
    assert vector_chunk.chunk_id == "a.md#0"
    assert vector_chunk.allowed_groups == ["eng"]
    assert embedding == [float(len("Some content here."))]
    assert keyword_index.upserted[0].chunk_id == "a.md#0"


def test_run_ingest_processes_every_document_and_every_chunk():
    connector = FakeConnector(
        [
            Document("a.md", "a.md", "# H1\nBody one.\n\n# H2\nBody two.", ["eng"]),
            Document("b.md", "b.md", "Just one chunk.", ["finance"]),
        ]
    )
    embedder = FakeEmbedder()
    vector_index = FakeVectorIndex()
    keyword_index = FakeKeywordIndex()

    chunk_count = run_ingest(connector, embedder, vector_index, keyword_index)

    assert chunk_count == 3  # two chunks from a.md's two headings + one from b.md
    chunk_ids = {chunk.chunk_id for chunk, _ in vector_index.upserted}
    assert chunk_ids == {"a.md#0", "a.md#1", "b.md#0"}


def test_run_ingest_with_no_documents_writes_nothing():
    chunk_count = run_ingest(
        FakeConnector([]), FakeEmbedder(), FakeVectorIndex(), FakeKeywordIndex()
    )

    assert chunk_count == 0


def test_reingesting_a_shrunk_document_deletes_the_orphaned_chunks_from_both_indexes():
    """Regression test for the gap this PR fixes: re-running ingest on a
    document that now produces fewer chunks must delete the extra
    chunk_ids a previous run wrote, from both indexes, not just leave them
    stale-but-retrievable.
    """
    vector_index = FakeVectorIndex()
    keyword_index = FakeKeywordIndex()

    first_run_text = "# H1\nBody one.\n\n# H2\nBody two.\n\n# H3\nBody three."
    run_ingest(
        FakeConnector([Document("a.md", "a.md", first_run_text, ["eng"])]),
        FakeEmbedder(),
        vector_index,
        keyword_index,
    )
    assert set(vector_index.by_chunk_id) == {"a.md#0", "a.md#1", "a.md#2"}
    assert set(keyword_index.by_chunk_id) == {"a.md#0", "a.md#1", "a.md#2"}

    shrunk_text = "# H1\nBody one only now."
    run_ingest(
        FakeConnector([Document("a.md", "a.md", shrunk_text, ["eng"])]),
        FakeEmbedder(),
        vector_index,
        keyword_index,
    )

    assert set(vector_index.by_chunk_id) == {"a.md#0"}
    assert set(keyword_index.by_chunk_id) == {"a.md#0"}
    assert vector_index.by_chunk_id["a.md#0"].text == "# H1\nBody one only now."


def test_reingesting_with_a_document_removed_deletes_all_its_chunks_and_acl_from_both_indexes():
    """Regression test for the more severe half of the same gap: a
    document deleted from the connector's source (e.g. removed from
    manifest.yaml, or its access revoked) must have all of its chunks -
    and their now-stale allowed_groups - removed from both indexes, not
    stay live and citable indefinitely.
    """
    vector_index = FakeVectorIndex()
    keyword_index = FakeKeywordIndex()

    run_ingest(
        FakeConnector([Document("secret.md", "secret.md", "Sensitive content.", ["finance"])]),
        FakeEmbedder(),
        vector_index,
        keyword_index,
    )
    assert set(vector_index.by_chunk_id) == {"secret.md#0"}

    # "secret.md" is now gone from what the connector returns entirely -
    # e.g. removed from the manifest, or the finance group's access to it
    # was revoked upstream.
    run_ingest(FakeConnector([]), FakeEmbedder(), vector_index, keyword_index)

    assert vector_index.by_chunk_id == {}
    assert keyword_index.by_chunk_id == {}


def test_stale_chunk_only_present_in_one_index_is_still_deleted_from_both():
    """The diff checks the UNION of what each index reports, not just one
    side - so a chunk that's already drifted out of sync between the two
    indexes (present in one, missing in the other, e.g. from a prior
    partial failure) still gets a delete issued to both, rather than the
    drift going undetected because only one index was consulted.
    """
    vector_index = FakeVectorIndex()
    keyword_index = FakeKeywordIndex()

    # Simulate drift directly: a stale chunk sitting only in the vector
    # index, with nothing in the keyword index for the same source.
    stale = IndexedChunk(chunk_id="a.md#5", source="a.md", text="stale", allowed_groups=["eng"])
    vector_index.by_chunk_id["a.md#5"] = stale

    run_ingest(
        FakeConnector([Document("a.md", "a.md", "Just one chunk now.", ["eng"])]),
        FakeEmbedder(),
        vector_index,
        keyword_index,
    )

    assert "a.md#5" not in vector_index.by_chunk_id
    assert set(vector_index.by_chunk_id) == {"a.md#0"}


def test_two_separate_run_ingest_calls_sharing_indexes_wipe_the_first_connectors_content():
    """Documents a real gotcha discovered live while adding a second
    connector (the web crawler) alongside FilesystemConnector: run_ingest()
    treats its connector's output as the COMPLETE set of currently-live
    sources for this run, and deletes anything indexed but not in that set
    (delete-propagation, see the module docstring). Calling it twice
    against the SAME indexes - once per connector - breaks that contract:
    the second call's connector has no way to know about the first
    connector's sources, so they look removed and get deleted. This is why
    ingest/cli.py combines every connector via CompositeConnector into ONE
    run_ingest() call instead - see the next test and composite.py.
    """
    vector_index = FakeVectorIndex()
    keyword_index = FakeKeywordIndex()

    run_ingest(
        FakeConnector([Document("a.md", "a.md", "From connector A.", ["eng"])]),
        FakeEmbedder(),
        vector_index,
        keyword_index,
    )
    assert set(vector_index.by_chunk_id) == {"a.md#0"}

    # A second, unrelated connector's run against the same indexes - it
    # legitimately has nothing to report, but that's indistinguishable
    # from "everything else was removed" from run_ingest()'s point of view.
    run_ingest(FakeConnector([]), FakeEmbedder(), vector_index, keyword_index)

    assert vector_index.by_chunk_id == {}


def test_composite_connector_lets_two_connectors_share_indexes_safely():
    """The fix/correct usage for the gotcha above: combine every connector
    via CompositeConnector and call run_ingest() once, so the "currently
    live sources" set run_ingest() diffs against is the true union across
    every connector, not just one.
    """
    vector_index = FakeVectorIndex()
    keyword_index = FakeKeywordIndex()

    connector_a = FakeConnector([Document("a.md", "a.md", "From connector A.", ["eng"])])
    connector_b = FakeConnector([Document("b.md", "b.md", "From connector B.", ["finance"])])
    run_ingest(
        CompositeConnector([connector_a, connector_b]),
        FakeEmbedder(),
        vector_index,
        keyword_index,
    )
    assert set(vector_index.by_chunk_id) == {"a.md#0", "b.md#0"}

    # Connector B now reports nothing (e.g. its one document was removed
    # upstream) - only b.md's chunks should go, a.md's must survive.
    run_ingest(
        CompositeConnector([connector_a, FakeConnector([])]),
        FakeEmbedder(),
        vector_index,
        keyword_index,
    )

    assert set(vector_index.by_chunk_id) == {"a.md#0"}
