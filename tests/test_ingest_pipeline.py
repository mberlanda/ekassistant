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
