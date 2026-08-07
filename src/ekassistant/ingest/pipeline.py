"""Batch ingest pipeline: load documents, chunk, embed, write to both indexes.

See docs/design/ingest.md#component-boundaries. Both indexes are written
per chunk, back to back, so they can never observe a chunk present in one
store but not the other for longer than a single chunk's write time - see
docs/decisions/0005-vector-and-keyword-store-choice.md's consistency
discussion.
"""

from typing import Protocol

from ekassistant.index.types import IndexedChunk
from ekassistant.ingest.chunker import chunk_document
from ekassistant.ingest.connectors.filesystem import Document


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class VectorIndexWriter(Protocol):
    def upsert(self, chunk: IndexedChunk, embedding: list[float]) -> None: ...


class KeywordIndexWriter(Protocol):
    def upsert(self, chunk: IndexedChunk) -> None: ...


class DocumentSource(Protocol):
    def load_documents(self) -> list[Document]: ...


def run_ingest(
    connector: DocumentSource,
    embed_client: Embedder,
    vector_index: VectorIndexWriter,
    keyword_index: KeywordIndexWriter,
) -> int:
    """Runs the full batch ingest, returns the number of chunks written."""
    chunk_count = 0
    for document in connector.load_documents():
        chunks = chunk_document(
            document.doc_id, document.source, document.text, document.allowed_groups
        )
        for chunk in chunks:
            embedding = embed_client.embed(chunk.text)
            vector_index.upsert(chunk, embedding)
            keyword_index.upsert(chunk)
            chunk_count += 1
    return chunk_count
