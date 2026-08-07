"""SQLite FTS5-backed keyword index.

See docs/decisions/0005-vector-and-keyword-store-choice.md. ACL filtering
(docs/decisions/0002-acl-enforcement-at-retrieval.md) happens inside the
same SQL query as the search, via a join against a group membership table,
never as a post-filter step.
"""

import sqlite3
from pathlib import Path

from ekassistant.config.settings import Settings
from ekassistant.index.types import IndexedChunk, SearchResult

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    source UNINDEXED,
    body
);
CREATE TABLE IF NOT EXISTS chunk_groups (
    chunk_id TEXT NOT NULL,
    group_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_groups_group ON chunk_groups(group_id);
CREATE INDEX IF NOT EXISTS idx_chunk_groups_chunk ON chunk_groups(chunk_id);
"""


def _to_match_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression.

    Each token is quoted individually and OR-joined, so the query is
    "does any of these words appear" ranked by bm25(), rather than letting
    user/document text be interpreted as FTS5's own query syntax (AND, OR,
    NOT, *, column filters, ...) - a query containing those words as plain
    text must not change how the search behaves.
    """
    tokens = text.split()
    if not tokens:
        return None
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


class SqliteKeywordIndex:
    # sqlite3 connections are only usable from the thread that created them
    # (check_same_thread defaults to True). Fine for the single-threaded
    # ingest/test usage in this PR; whichever later PR wires this into the
    # (likely multi-threaded) API Gateway needs to either open one
    # connection per call or per request, not share this instance across
    # threads as-is.
    def __init__(self, settings: Settings):
        path = Path(settings.keyword_index_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(self, chunk: IndexedChunk) -> None:
        # Delete-then-insert in one transaction, not by calling delete()
        # (which commits on its own): two separate commits would leave a
        # window where a re-ingested chunk is transiently absent between
        # them if a search runs in between.
        with self._conn:
            self._delete_within_transaction(chunk.chunk_id)
            self._conn.execute(
                "INSERT INTO chunks_fts(chunk_id, source, body) VALUES (?, ?, ?)",
                (chunk.chunk_id, chunk.source, chunk.text),
            )
            self._conn.executemany(
                "INSERT INTO chunk_groups(chunk_id, group_id) VALUES (?, ?)",
                [(chunk.chunk_id, group_id) for group_id in chunk.allowed_groups],
            )

    def delete(self, chunk_id: str) -> None:
        with self._conn:
            self._delete_within_transaction(chunk_id)

    def _delete_within_transaction(self, chunk_id: str) -> None:
        self._conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
        self._conn.execute("DELETE FROM chunk_groups WHERE chunk_id = ?", (chunk_id,))

    def chunk_ids_for_source(self, source: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT chunk_id FROM chunks_fts WHERE source = ?", (source,)
        ).fetchall()
        return {row[0] for row in rows}

    def all_sources(self) -> set[str]:
        rows = self._conn.execute("SELECT DISTINCT source FROM chunks_fts").fetchall()
        return {row[0] for row in rows}

    def search(self, query_text: str, allowed_groups: list[str], top_n: int) -> list[SearchResult]:
        # Fail closed (docs/decisions/0002): no groups means nothing is
        # visible, full stop - don't even issue the query.
        if not allowed_groups:
            return []

        match_query = _to_match_query(query_text)
        if match_query is None:
            return []

        placeholders = ", ".join("?" for _ in allowed_groups)
        sql = f"""
            SELECT f.chunk_id, f.source, f.body, bm25(chunks_fts) AS raw_score
            FROM chunks_fts f
            WHERE f.body MATCH ?
              AND f.chunk_id IN (
                  SELECT DISTINCT chunk_id FROM chunk_groups WHERE group_id IN ({placeholders})
              )
            ORDER BY raw_score
            LIMIT ?
        """
        params = [match_query, *allowed_groups, top_n]
        rows = self._conn.execute(sql, params).fetchall()
        # bm25() is lower-is-better (more negative = more relevant); negate
        # so SearchResult.score is higher-is-better like the vector index.
        return [
            SearchResult(chunk_id=chunk_id, source=source, text=body, score=-raw_score)
            for chunk_id, source, body, raw_score in rows
        ]
