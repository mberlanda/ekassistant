# Low-level design: Ingest

Pipeline stage `a` in [the brief](../context/brief.md). Turns raw source
content into searchable, Access Control List (ACL)-tagged chunks in the
[vector + keyword indexes](retrieval.md). Runs per source, both as a batch
job (full resync) and incrementally via Change Data Capture (CDC).

## Responsibilities

1. **Connect** to a source (wiki, share drive, customer support ticket
   system, chat) and enumerate documents, either as a full listing (batch)
   or as a since-last-sync delta (CDC: created/updated/deleted since a
   checkpoint).
2. **Parse** each document from its native format (HTML, Markdown, PDF,
   plain text, chat transcript JSON, ...) into a normalized text
   representation plus structural metadata (headings, sections).
3. **Chunk** the normalized text in a structure-aware way — splitting on
   natural boundaries (headings, paragraphs, ticket comments) rather than
   fixed character counts, so a chunk is a coherent, citable unit.
4. **Embed** each chunk using the local embedding model
   (see [ADR-0006](../decisions/0006-embedding-model-choice.md)) — this
   happens inside the trust boundary, never via a hosted API.
5. **Attach ACL metadata**: the source's access control list (which
   group IDs may see this document), resolved to this project's group ID
   space, stored alongside each chunk.
6. **Write** each chunk + embedding + ACL metadata to both the vector index
   and the keyword index (see [ADR-0005](../decisions/0005-vector-and-keyword-store-choice.md)),
   and **propagate deletes** the same way when CDC reports a document
   removed or access-revoked.

## Component boundaries

```
Connector (per source)  --raw docs-->  Parser  --normalized text-->  Chunker
                                                                        |
                                                                        v
                                                          Embedder (local, in trust boundary)
                                                                        |
                                                                        v
                                                          ACL tagger (source ACL -> group IDs)
                                                                        |
                                                                        v
                                                   Index writer (vector index + keyword index, both or neither)
```

Each connector implements one interface (`list_documents`,
`list_changes_since(checkpoint)`, `fetch(document_id)`) so parsing,
chunking, embedding, and writing are shared code across every source —
only the connector is source-specific.

### Web crawler connector

See [ADR-0010](../decisions/0010-web-crawler-connector.md) for the full
decision record. `WebCrawlerConnector` implements the same
`DocumentSource` interface as `FilesystemConnector` (`load_documents()`),
so it's a second, drop-in source for `run_ingest` - chunking, embedding,
indexing, and delete-propagation are unaffected.

```
config/crawl_targets.yaml (seed URL -> allowed_groups + crawl mode)
        |
        v
robots.txt check (per origin) --> HTTP fetch --> html_parser.parse_html()
        |                                                |
        | (same_origin mode only: follow same-origin     v
        |  links, bounded by max_depth/max_pages)   Document(doc_id=url, ...)
        v
   next URL to fetch
```

Two crawl modes, chosen per target: `single_page` (default - fetch
exactly the seed URL, follow nothing) and `same_origin` (opt-in - BFS
same-origin links, hard-capped by `max_depth`/`max_pages`). Unlike every
other source, crawled HTML carries no native ACL metadata, so
`allowed_groups` is config-driven per target and fails closed (an
unmapped domain is never fetched) - see ADR-0010 for why a single global
default group was rejected. `config/crawl_targets.yaml` ships empty; no
domain is baked into the connector itself.

## Tradeoffs

- **Structure-aware chunking vs. fixed-size chunking**: fixed-size (e.g.
  every 500 characters) is simpler and source-agnostic, but routinely
  splits a citable unit (a ticket comment, a heading's content) across two
  chunks, which weakens both keyword matching and citation precision.
  Structure-aware chunking is more implementation work per source format
  but produces chunks that are actually coherent things to cite — chosen
  for that reason, at the cost of needing a real parser per source format
  instead of one generic splitter.
- **Per-source connectors vs. one generic ingestion path**: a single
  generic connector (e.g. "anything reachable over HTTP") would be less
  code, but real sources differ enough in auth, pagination, ACL
  representation, and change-detection semantics that a generic connector
  would need source-specific branches internally anyway — the per-source
  connector interface just makes that explicit instead of hidden inside
  one large function.
- **Fail-closed ACL tagging**: a document with missing or unparseable ACL
  metadata is treated as **inaccessible to everyone**, not public — this
  follows directly from [ADR-0002](../decisions/0002-acl-enforcement-at-retrieval.md)
  and trades ingestion completeness (some documents may silently not
  appear in results) for never leaking a document whose access rules
  couldn't be determined.
- **Dual-write to two index types**: writing each chunk to both the vector
  and keyword index in one logical operation (not two independently
  scheduled jobs) trades a bit of ingest-path complexity for avoiding the
  two indexes drifting out of sync with each other — see the consistency
  discussion in [ADR-0005](../decisions/0005-vector-and-keyword-store-choice.md).

## Open questions for V2+

- Batch vs. streaming CDC cadence per source (polling interval vs. a real
  change feed/webhook) is source-dependent and deferred until a specific
  second/third source is connected.
- Data Loss Prevention (DLP) / de-identification of sensitive content
  before indexing is a [data layer](data-layer.md) / [governance](governance.md)
  concern that ingest will need to call into once in scope — not yet
  implemented in V1.
