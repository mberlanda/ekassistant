"""Shared types for the vector and keyword index adapters.

See docs/design/retrieval.md#component-boundaries: both adapters are
written against the same chunk/result shapes so retrieval (and, upstream,
ingest) doesn't need to know which concrete store it's talking to.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IndexedChunk:
    """What ingest writes. `allowed_groups=[]` means visible to no one -
    see docs/decisions/0002-acl-enforcement-at-retrieval.md: missing/empty
    ACL metadata fails closed, it is never treated as public.
    """

    chunk_id: str
    source: str
    text: str
    allowed_groups: list[str]


@dataclass(frozen=True)
class SearchResult:
    """What both adapters return from search(). `score` is always
    higher-is-better, regardless of the underlying store's native
    convention (Qdrant already is; SQLite FTS5's bm25() is negated to
    match - see keyword_index.py).
    """

    chunk_id: str
    source: str
    text: str
    score: float
