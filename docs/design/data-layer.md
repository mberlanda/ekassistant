# Low-level design: Data Layer

Reference spine item g. Everything that durably stores data: the document
store, the vector/keyword indexes' underlying storage, the audit log, and
feature/metadata storage. Where Data Loss Prevention (DLP) and
de-identification would run, if/when in scope.

## Responsibilities

1. **Document store**: the normalized, parsed form of each source document
   (post-parse, pre-chunk — see [ingest design](ingest.md)), kept so
   chunks can be traced back to their full source document for citation
   display and reprocessing (e.g. re-chunking with a different strategy
   without re-fetching from the source).
2. **Index storage**: the on-disk state of the vector index (Qdrant) and
   keyword index (SQLite FTS5) — see
   [ADR-0005](../decisions/0005-vector-and-keyword-store-choice.md).
3. **Audit log**: an append-only record of every query: who asked, what
   groups they had, what was retrieved (chunk IDs, post-ACL-filter), what
   was answered, and whether the system abstained. This is the record
   that lets a security review answer "did this user ever see document
   X" after the fact, not just at query time.
4. **Metadata/feature store**: source-level and document-level metadata
   used across ingest and retrieval (source type, last sync checkpoint,
   ACL group mappings) — kept separate from document content itself.
5. **DLP / de-identification** (V2+, not implemented in V1): scanning
   content for sensitive data (secrets, Personally Identifiable
   Information (PII)) before it's indexed or surfaced. Named in the
   reference spine; deferred because V1's few high-value sources are
   curated by the developer and the ACL pre-filter is the primary control
   for V1 — DLP becomes necessary once ingest is opened to broader,
   less-curated sources.

## Storage choices for V1

- Document store: flat files on disk (one normalized document per file,
  keyed by source + document ID) — no database needed yet for a
  few-source POC.
- Audit log: an append-only local file (structured, one JSON record per
  query) for V1; a real deployment would use a write-once store or a
  managed logging pipeline instead — see [tradeoffs](#tradeoffs).
- Metadata/feature store: a single SQLite database (separate from the
  FTS5 keyword index's database, kept logically distinct) for source
  configs, sync checkpoints, and the ACL group mapping table.

## Tradeoffs

- **Flat files over a database for the document store**: trivial to
  inspect and version for a POC, but no transactional guarantees and no
  query capability beyond "fetch by ID" — acceptable while the corpus is
  small and single-machine; a real deployment would use a document
  database once concurrent writers or richer queries are needed.
- **Audit log as a local file, not a managed/immutable store**: cheapest
  possible way to get "every query is recorded", but a local file can be
  edited or deleted by anyone with filesystem access, which is not
  acceptable for a real audit trail. This is explicitly a POC
  simplification, flagged here so it isn't mistaken for a compliance-grade
  audit log — see [governance design](governance.md) for what a real audit
  log needs (tamper-evidence, retention policy, access control on the log
  itself).
- **Separating document store from index storage**: keeping the
  normalized document separate from its embedded/indexed chunks costs an
  extra store to keep in sync, but means re-chunking or re-embedding
  (e.g. after [ADR-0006](../decisions/0006-embedding-model-choice.md) is
  revisited) never requires re-fetching from the original source.
