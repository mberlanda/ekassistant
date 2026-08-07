from ekassistant.index.types import IndexedChunk
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


class FakeVectorIndex:
    def __init__(self):
        self.upserted: list[tuple[IndexedChunk, list[float]]] = []

    def upsert(self, chunk: IndexedChunk, embedding: list[float]) -> None:
        self.upserted.append((chunk, embedding))


class FakeKeywordIndex:
    def __init__(self):
        self.upserted: list[IndexedChunk] = []

    def upsert(self, chunk: IndexedChunk) -> None:
        self.upserted.append(chunk)


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


class FakeKeyedVectorIndex:
    """Models a real index's upsert-by-chunk_id semantics (unlike
    FakeVectorIndex's append-only log above), so re-running ingest against
    it behaves like a real index would.
    """

    def __init__(self):
        self.by_chunk_id: dict[str, IndexedChunk] = {}

    def upsert(self, chunk: IndexedChunk, embedding: list[float]) -> None:
        self.by_chunk_id[chunk.chunk_id] = chunk


def test_KNOWN_GAP_reingesting_a_shrunk_document_leaves_orphaned_chunks():
    """Characterizes the documented gap in pipeline.py's module docstring:
    re-running ingest on a document that now produces fewer chunks does
    NOT delete the extra chunk_ids a previous run wrote. This test exists
    so the gap is a visible, intentional fact of current behavior - not
    silently assumed away - and so whichever PR fixes it has a test to
    flip instead of a bug to rediscover from scratch.
    """
    vector_index = FakeKeyedVectorIndex()

    first_run_text = "# H1\nBody one.\n\n# H2\nBody two.\n\n# H3\nBody three."
    run_ingest(
        FakeConnector([Document("a.md", "a.md", first_run_text, ["eng"])]),
        FakeEmbedder(),
        vector_index,
        FakeKeywordIndex(),
    )
    assert set(vector_index.by_chunk_id) == {"a.md#0", "a.md#1", "a.md#2"}

    shrunk_text = "# H1\nBody one only now."
    run_ingest(
        FakeConnector([Document("a.md", "a.md", shrunk_text, ["eng"])]),
        FakeEmbedder(),
        vector_index,
        FakeKeywordIndex(),
    )

    # "a.md#1" and "a.md#2" are stale (from content that no longer exists)
    # but still present - this is the gap, not the desired end state.
    assert set(vector_index.by_chunk_id) == {"a.md#0", "a.md#1", "a.md#2"}
    assert vector_index.by_chunk_id["a.md#0"].text == "# H1\nBody one only now."


def test_KNOWN_GAP_removing_a_document_entirely_leaves_its_chunks_and_acl_live():
    """The more severe half of the same gap: a document deleted from the
    connector's source (e.g. removed from manifest.yaml, or its access
    revoked) is never revisited by load_documents() again, so its chunks -
    with their ORIGINAL allowed_groups - are never removed. This is an
    access-revocation staleness case, not just a relevance one.
    """
    vector_index = FakeKeyedVectorIndex()

    run_ingest(
        FakeConnector([Document("secret.md", "secret.md", "Sensitive content.", ["finance"])]),
        FakeEmbedder(),
        vector_index,
        FakeKeywordIndex(),
    )
    assert set(vector_index.by_chunk_id) == {"secret.md#0"}

    # "secret.md" is now gone from what the connector returns entirely -
    # e.g. removed from the manifest, or the finance group's access to it
    # was revoked upstream.
    run_ingest(FakeConnector([]), FakeEmbedder(), vector_index, FakeKeywordIndex())

    # Still there, still tagged for a group that (in the revocation
    # scenario) should no longer see it - this is the gap.
    assert set(vector_index.by_chunk_id) == {"secret.md#0"}
    assert vector_index.by_chunk_id["secret.md#0"].allowed_groups == ["finance"]
