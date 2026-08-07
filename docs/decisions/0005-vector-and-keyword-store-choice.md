# 0005. Use Qdrant for the vector index and SQLite FTS5 for the keyword index

Date: 2026-08-07

## Status

Accepted

## Context

Pipeline stage `b` needs both a vector index (for dense/embedding search)
and a keyword index (for Best Matching 25 (BM25) search), both filterable
by Access Control List (ACL) metadata as a pre-filter
(see [ADR-0002](0002-acl-enforcement-at-retrieval.md)), and both able to
handle incremental updates and delete-propagation from Change Data Capture
(CDC) rather than requiring a full reindex per source refresh.

The developer has OrbStack available for running Docker containers, but is
otherwise working solo on a POC and wants to minimize operational surface
area (fewer moving containers, faster local bring-up).

## Decision

- **Vector index: [Qdrant](https://qdrant.tech)**, run as a single container
  via `docker-compose.yml` (see [ADR-0001 in the compose
  file](../../docker-compose.yml)). Qdrant supports native payload
  (metadata) filtering combined with vector search in one query — exactly
  what the ACL pre-filter needs — plus per-point upsert and delete, which
  maps directly onto CDC incremental updates and delete-propagation.
- **Keyword index: SQLite's built-in FTS5 extension**, embedded in-process
  (no container). FTS5 ships a native `bm25()` ranking function, so it
  satisfies the BM25 half of hybrid search (see
  [ADR-0003](0003-hybrid-retrieval-with-rrf.md)) without adding a service.
  ACL metadata is stored as ordinary indexed columns alongside the FTS5
  virtual table and applied as a `WHERE` pre-filter on the same query.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Elasticsearch / OpenSearch for both vector *and* keyword (unified store) | One system for hybrid search; mature filtering, aggregations, and operational tooling; what a production deployment would likely converge on | Heavyweight for a solo POC (JVM-based, higher memory footprint, more container config); duplicates what SQLite FTS5 already gives for free at this scale |
| Vector library only (e.g. FAISS) instead of Qdrant | No server process; very fast for pure similarity search | No native metadata filtering combined with the vector search itself — ACL pre-filtering would have to happen as a separate step, reintroducing exactly the post-filter risk [ADR-0002](0002-acl-enforcement-at-retrieval.md) rejects; no built-in upsert/delete story for CDC |
| Postgres with `pgvector` + native full-text search for both | One database, one container, transactional consistency between the two indexes | Adds a full relational database as infrastructure for a POC that doesn't need one yet; `pgvector`'s ANN performance and filtering ergonomics are behind purpose-built vector databases like Qdrant |
| Qdrant (vector) + SQLite FTS5 (keyword) — chosen | One container instead of two-plus; SQLite FTS5 is embedded (zero ops); Qdrant's filtered search directly satisfies the ACL pre-filter requirement; both sides support incremental upsert/delete for CDC | Two different systems with two different consistency/backup stories instead of one; SQLite FTS5 will not scale past a single-machine, moderate-corpus-size POC; keyword and vector indexes can drift out of sync with each other if ingest isn't written carefully (see [ingest design](../design/ingest.md)) |

## Tradeoffs of the chosen option

Splitting vector and keyword storage across two different systems is a
deliberate scope-appropriate simplification, not a long-term architecture
bet: it avoids running a heavyweight search cluster for a few-source POC,
at the cost of two independent write paths that ingest must keep
consistent (same document ID, same ACL metadata, same delete-propagation
behavior, applied to both). SQLite FTS5 in particular has no built-in
answer for multi-writer concurrency or horizontal scale — acceptable for a
single-developer POC with batch + light CDC ingest, wrong once ingest
volume or concurrent query load grows. Migrating the keyword index to
OpenSearch/Elasticsearch later is the expected upgrade path and is called
out explicitly so it isn't a surprise.

## Consequences

- The ingest layer's index-writer step (see [ingest design](../design/ingest.md))
  must write to both stores in a way that keeps them consistent per
  document/chunk ID — a shared "delete-then-upsert-both" contract, not two
  independently-triggered writes.
- `docker-compose.yml` only needs to bring up Qdrant; SQLite FTS5 requires
  no container, just a file path in [settings](../../src/ekassistant/config/settings.py).
- If corpus size or write concurrency outgrows SQLite FTS5, the documented
  upgrade path is to replace only the keyword-index adapter with
  OpenSearch/Elasticsearch — the retrieval interface (see
  [retrieval design](../design/retrieval.md)) is written against an
  abstract keyword-search port specifically so this swap doesn't ripple
  into the rest of the system.
