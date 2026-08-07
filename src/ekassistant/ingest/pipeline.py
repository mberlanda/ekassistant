"""Batch ingest pipeline: load documents, chunk, embed, write to both indexes.

See docs/design/ingest.md#component-boundaries. Both indexes are written
per chunk, back to back, so they can never observe a chunk present in one
store but not the other for longer than a single chunk's write time - see
docs/decisions/0005-vector-and-keyword-store-choice.md's consistency
discussion.

KNOWN GAP: re-running ingest on a document that now chunks into FEWER
pieces than a previous run leaves the extra old chunk_ids (e.g. "doc.md#5"
when the doc now only produces "doc.md#0".."doc.md#3") orphaned in both
indexes indefinitely - nothing here deletes a chunk_id that the current
run no longer produces. Harmless for the static seed corpus this PR ships
(chunk count never changes), but a real blocker for any source that gets
re-ingested after edits. Fixing it needs either a "list chunk_ids for this
doc_id" query on both index adapters or a stored high-water-mark per
document - deferred rather than solved here (docs/design/ingest.md already
scopes CDC/delete-propagation cadence as an open V2+ question); see
tests/test_ingest_pipeline.py's characterization test for the exact
behavior this leaves in place.
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
