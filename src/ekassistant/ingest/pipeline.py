"""Batch ingest pipeline: load documents, chunk, embed, write to both indexes.

See docs/design/ingest.md#component-boundaries. Both indexes are written
per chunk, back to back, so they can never observe a chunk present in one
store but not the other for longer than a single chunk's write time - see
docs/decisions/0005-vector-and-keyword-store-choice.md's consistency
discussion.

KNOWN GAP: this pipeline never diffs the current run's chunk_ids against
a previous run's. Two consequences, in increasing order of severity:

  1. A document that now chunks into FEWER pieces (edited shorter) leaves
     the extra old chunk_ids (e.g. "doc.md#5" when the doc now only
     produces "doc.md#0".."doc.md#3") orphaned in both indexes - stale
     but harmless-ish content stays retrievable.
  2. A document REMOVED from the connector entirely (deleted from
     manifest.yaml, or access revoked) is never revisited by
     load_documents() at all, so 100% of its chunks - with their
     original allowed_groups - stay live and citable indefinitely. This
     is an access-revocation staleness case, not just a relevance one:
     docs/design/ingest.md's Responsibilities explicitly names delete
     propagation "on access-revoked" as core scope, so this is the more
     serious half of the gap even though both share the same root cause.

Harmless for the static seed corpus this PR ships (nothing shrinks or
disappears between runs), but a real blocker for any source re-ingested
after edits or ACL changes. Fixing it needs either a "list chunk_ids for
this doc_id" query on both index adapters or a stored high-water-mark /
known-doc-id-set per source - deferred rather than solved here
(docs/design/ingest.md already scopes CDC/delete-propagation cadence as
an open V2+ question); see tests/test_ingest_pipeline.py's characterization
tests for the exact behavior this leaves in place for both cases.
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
