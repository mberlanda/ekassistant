"""Batch ingest pipeline: load documents, chunk, embed, write to both indexes.

See docs/design/ingest.md#component-boundaries and
docs/design/governance.md's ACL-propagation responsibility. Both indexes
are written per chunk, back to back, so they can never observe a chunk
present in one store but not the other for longer than a single chunk's
write time - see docs/decisions/0005-vector-and-keyword-store-choice.md's
consistency discussion.

DELETE PROPAGATION: each run diffs the chunk_ids it's about to write
against what's already indexed for that source, and removes whatever's
left over from both indexes. This closes two cases docs/design/ingest.md's
Responsibilities #6 names as in-scope:

  1. A document that now chunks into FEWER pieces (edited shorter) - the
     extra old chunk_ids (e.g. "doc.md#3" when the doc now only produces
     "doc.md#0".."doc.md#1") are deleted, not left orphaned.
  2. A document REMOVED from the connector entirely (deleted from
     manifest.yaml, or access revoked) - after processing every document
     load_documents() *did* return, any source still recorded in either
     index but absent from this run's output has all of its chunks
     deleted from both indexes. This is the access-revocation case:
     without it, a revoked group would keep being able to retrieve
     content indefinitely, which is an authorization bug, not just a
     relevance one.

Both diffs query the union of what EACH index reports for a source/across
all sources (`chunk_ids_for_source`/`all_sources`), not just one - so a
chunk already drifted out of sync between the two (present in one,
missing in the other) still gets a delete issued to both, self-healing
that drift rather than only ever detecting it from one side. See
tests/test_ingest_pipeline.py (mocked) and tests/test_governance.py (live,
against real Qdrant + SQLite) for the exact behavior.

Cost note: this means every ingest run does a filtered scan per source
(via chunk_ids_for_source, on every document) plus one more full-collection
scan per index for the removed-source check (via all_sources, once per
run) - which is fine at this project's seed_corpus scale but wouldn't be
for a large, frequently re-ingested corpus - a stored high-water-mark/
known-chunk-id-set per source would avoid the repeated scans if that
becomes a real cost.
"""

from typing import Protocol

from ekassistant.index.types import IndexedChunk
from ekassistant.ingest.chunker import chunk_document
from ekassistant.ingest.connectors.filesystem import Document
from ekassistant.models.embedder import Embedder


class VectorIndexWriter(Protocol):
    def upsert(self, chunk: IndexedChunk, embedding: list[float]) -> None: ...
    def delete(self, chunk_id: str) -> None: ...
    def chunk_ids_for_source(self, source: str) -> set[str]: ...
    def all_sources(self) -> set[str]: ...


class KeywordIndexWriter(Protocol):
    def upsert(self, chunk: IndexedChunk) -> None: ...
    def delete(self, chunk_id: str) -> None: ...
    def chunk_ids_for_source(self, source: str) -> set[str]: ...
    def all_sources(self) -> set[str]: ...


class DocumentSource(Protocol):
    def load_documents(self) -> list[Document]: ...


def run_ingest(
    connector: DocumentSource,
    embed_client: Embedder,
    vector_index: VectorIndexWriter,
    keyword_index: KeywordIndexWriter,
) -> int:
    """Runs the full batch ingest, returns the number of chunks written.

    Also propagates deletes for stale chunks and fully-removed sources -
    see the module docstring.
    """
    chunk_count = 0
    current_sources: set[str] = set()
    for document in connector.load_documents():
        current_sources.add(document.source)
        chunks = chunk_document(
            document.doc_id, document.source, document.text, document.allowed_groups
        )
        new_chunk_ids = {chunk.chunk_id for chunk in chunks}
        for chunk in chunks:
            embedding = embed_client.embed(chunk.text)
            vector_index.upsert(chunk, embedding)
            keyword_index.upsert(chunk)
            chunk_count += 1
        _delete_stale_chunks(document.source, new_chunk_ids, vector_index, keyword_index)

    _delete_removed_sources(current_sources, vector_index, keyword_index)
    return chunk_count


def _delete_stale_chunks(
    source: str,
    live_chunk_ids: set[str],
    vector_index: VectorIndexWriter,
    keyword_index: KeywordIndexWriter,
) -> None:
    already_indexed = vector_index.chunk_ids_for_source(
        source
    ) | keyword_index.chunk_ids_for_source(source)
    for chunk_id in already_indexed - live_chunk_ids:
        vector_index.delete(chunk_id)
        keyword_index.delete(chunk_id)


def _delete_removed_sources(
    current_sources: set[str],
    vector_index: VectorIndexWriter,
    keyword_index: KeywordIndexWriter,
) -> None:
    previously_indexed_sources = vector_index.all_sources() | keyword_index.all_sources()
    for source in previously_indexed_sources - current_sources:
        _delete_stale_chunks(
            source, live_chunk_ids=set(), vector_index=vector_index, keyword_index=keyword_index
        )
