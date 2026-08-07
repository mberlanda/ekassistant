"""Qdrant-backed vector index.

See docs/decisions/0005-vector-and-keyword-store-choice.md. ACL filtering
(docs/decisions/0002-acl-enforcement-at-retrieval.md) happens inside the
Qdrant query itself via a payload filter, never as a post-filter step.
"""

import uuid

from qdrant_client import QdrantClient, models

from ekassistant.config.settings import Settings
from ekassistant.index.types import IndexedChunk, SearchResult

# Fixed namespace so chunk_id -> point_id is a deterministic pure function:
# Qdrant point IDs must be an unsigned int or a UUID, never an arbitrary
# string, so chunk_id (an arbitrary string minted by ingest) can't be used
# directly. Deriving the UUID this way means delete(chunk_id) never needs
# a lookup - it recomputes the same ID upsert used.
_POINT_ID_NAMESPACE = uuid.UUID("f47c1b1e-3c1a-4b8e-8f2a-6d9f9e6f0a1a")


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, chunk_id))


class QdrantVectorIndex:
    def __init__(self, settings: Settings):
        self._client = QdrantClient(url=settings.qdrant_url)
        self._collection = settings.qdrant_collection
        self._vector_size = settings.embedding_dimensions

    def ensure_collection(self) -> None:
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(
                    size=self._vector_size, distance=models.Distance.COSINE
                ),
            )

    def upsert(self, chunk: IndexedChunk, embedding: list[float]) -> None:
        self._client.upsert(
            collection_name=self._collection,
            points=[
                models.PointStruct(
                    id=_point_id(chunk.chunk_id),
                    vector=embedding,
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "source": chunk.source,
                        "text": chunk.text,
                        "allowed_groups": chunk.allowed_groups,
                    },
                )
            ],
        )

    def delete(self, chunk_id: str) -> None:
        self._client.delete(
            collection_name=self._collection,
            points_selector=models.PointIdsList(points=[_point_id(chunk_id)]),
        )

    def chunk_ids_for_source(self, source: str) -> set[str]:
        source_filter = models.Filter(
            must=[models.FieldCondition(key="source", match=models.MatchValue(value=source))]
        )
        return {
            point.payload["chunk_id"]
            for point in self._scroll_all(scroll_filter=source_filter)
        }

    def all_sources(self) -> set[str]:
        return {point.payload["source"] for point in self._scroll_all()}

    def _scroll_all(self, scroll_filter: models.Filter | None = None) -> list[models.Record]:
        # scroll() paginates (default page size well under our POC corpus
        # sizes) - loop until Qdrant reports no further offset rather than
        # assuming one page covers everything.
        records: list[models.Record] = []
        offset = None
        while True:
            page, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=scroll_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            records.extend(page)
            if offset is None:
                break
        return records

    def search(
        self, query_embedding: list[float], allowed_groups: list[str], top_n: int
    ) -> list[SearchResult]:
        # Fail closed (docs/decisions/0002): no groups means nothing is
        # visible, full stop - don't even issue the query.
        if not allowed_groups:
            return []

        acl_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="allowed_groups", match=models.MatchAny(any=allowed_groups)
                )
            ]
        )
        response = self._client.query_points(
            collection_name=self._collection,
            query=query_embedding,
            query_filter=acl_filter,
            limit=top_n,
        )
        return [
            SearchResult(
                chunk_id=point.payload["chunk_id"],
                source=point.payload["source"],
                text=point.payload["text"],
                score=point.score,
            )
            for point in response.points
        ]
