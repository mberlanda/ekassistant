# Roadmap

Living status tracker for what's built vs. designed-but-not-built. Updated
as each feature group merges. For *why* something is shaped the way it is,
follow the doc links, not this page — this page only tracks *what exists*.

Workflow for everything still "Not started": a feature branch, a PR against
`main`, two independent review passes (findings fixed between passes), then
merge — no direct-to-main commits for code once a component starts moving
past its skeleton stub. Docs-only updates (like this file) may still land
directly.

## Done

| Component | Docs | Status |
|---|---|---|
| ADRs + low-level designs + glossary + README | [docs/decisions/](decisions/), [docs/design/](design/) | Done |
| Project skeleton (`src/ekassistant/*`, one subpackage per spine component) | [docs/architecture.md](architecture.md) | Done |
| Settings (env-driven config) | [docs/design/api-gateway-identity.md](design/api-gateway-identity.md) | Done |
| Mock identity/group lookup (`identity/store.py` + `config/identities.yaml`) | [ADR-0007](decisions/0007-mock-identity-and-group-lookup.md) | Done, tested |
| API Gateway: `/health`, `/whoami` | [docs/design/api-gateway-identity.md](design/api-gateway-identity.md) | Done, tested |
| TUI: REPL, mock user switching (`:user <id>`) | [docs/design/client-tui.md](design/client-tui.md) | Done, tested |
| `docker-compose.yml` (Qdrant) | [ADR-0005](decisions/0005-vector-and-keyword-store-choice.md) | Done, verified running |
| Dev tooling (Makefile, ruff, pytest, `.env.example`) | — | Done |
| Ollama models pulled (`llama3.2:1b`, `nomic-embed-text`) | [ADR-0004](decisions/0004-local-llm-serving-via-ollama.md), [ADR-0006](decisions/0006-embedding-model-choice.md) | Done locally (not something a PR tracks — a machine-local step) |
| Model layer client (`OllamaClient`, `generate_answer` cite-or-abstain) | [docs/design/model-layer.md](design/model-layer.md), [ADR-0004](decisions/0004-local-llm-serving-via-ollama.md), [ADR-0006](decisions/0006-embedding-model-choice.md) | Done, tested, and verified live against the real pulled models ([PR #1](https://github.com/mberlanda/ekassistant/pull/1)). Citations are rebuilt from authoritative context, never trusted from the model's own output. Confirmed empirically: `llama3.2:1b` is exactly as unreliable as ADR-0004 predicted (inconsistent citations run-to-run, occasional wrong-but-structurally-valid answers) — the validation path correctly downgrades the invalid cases to abstain, but *answer correctness* on valid cases is a model-quality problem for the eval harness (item 8) to catch, not something this layer can fix |
| Index adapters (`QdrantVectorIndex`, `SqliteKeywordIndex`) | [docs/design/retrieval.md](design/retrieval.md), [ADR-0005](decisions/0005-vector-and-keyword-store-choice.md) | Done, tested, verified live against real Qdrant + real embeddings ([PR #2](https://github.com/mberlanda/ekassistant/pull/2)). ACL enforced as a native filter inside each query (Qdrant `MatchAny` payload filter; SQLite join against a `chunk_groups` table), never post-filtered; both `search()` calls fail closed on an empty group set. Independent review specifically fuzzed for ACL-bypass, SQL-injection, and FTS5-escaping risk and found none; added multi-group partial-overlap test coverage since that was the one real gap. `SqliteKeywordIndex` has a documented single-thread constraint (sqlite3's `check_same_thread` default) that whichever PR wires this into the API Gateway needs to account for |
| Ingest core + tiny seed corpus (`FilesystemConnector`, `chunk_document`, `run_ingest`, `ekassistant-ingest`) | [docs/design/ingest.md](design/ingest.md) | Done, tested, verified live end-to-end ([PR #3](https://github.com/mberlanda/ekassistant/pull/3)). Manifest-driven local connector + structure-aware Markdown chunker (splits on headings, then paragraphs) + per-chunk dual-index writer, run against `seed_corpus/`'s 4 hand-written docs (12 chunks). `make ingest` verified live: all four mock users' access matches their groups exactly, including guest (no groups) getting zero results. **Known, deliberately-deferred gap** (documented in `pipeline.py` and covered by characterization tests, not silently assumed away): re-running ingest never diffs against a prior run, so a shrunk or removed document's stale chunks - with their original ACL - are never deleted from either index. Real blocker for any source that gets re-ingested after edits/revocation; fine for this PR's static corpus. Revisit when a source with real churn is connected (item 4/5) or before ingest runs on a schedule |

## Not started

| # | Component | Docs | Description | Status |
|---|---|---|---|---|
| 4 | Ingest format adapters | [docs/design/ingest.md](design/ingest.md) | Parsers for `.txt`/Markdown, HTML, PDF, `.docx`, each with its own small sample doc; format choice and any sample-corpus licensing/provenance recorded in a new ADR | Not started |
| 5 | Ingest crawler adapter | [docs/design/ingest.md](design/ingest.md) (extended) | A connector that pulls pages from a small, scoped set of websites (robots.txt-respecting, allowlisted domains only) — needs its own ADR for scope/safety before implementation, since it's the one connector that reaches the open internet | Not started |
| 6 | Retrieval | [docs/design/retrieval.md](design/retrieval.md), [ADR-0002](decisions/0002-acl-enforcement-at-retrieval.md), [ADR-0003](decisions/0003-hybrid-retrieval-with-rrf.md) | ACL filter compilation, dense + BM25 hybrid search, RRF fusion, reranker interface (pass-through default until a cross-encoder model is pulled) | Not started |
| 7 | Orchestration + `/query` | [docs/design/orchestration.md](design/orchestration.md), [ADR-0008](decisions/0008-lightweight-orchestration.md) | Wire identity → retrieval → model layer into one request pipeline; add `POST /query` to the API Gateway; connect the TUI's question path (currently a stub message) | Not started |
| 8 | Observability + eval harness | [docs/design/observability.md](design/observability.md) | Per-run tracing, metrics (latency, abstain rate), a labeled eval question set including ACL test cases (a user asking about a document outside their groups) | Not started |
| 9 | Governance checks | [docs/design/governance.md](design/governance.md) | ACL-propagation regression checks (delete/revoke reaches both indexes); classification metadata is already captured at ingest (item 3/4) but nothing yet acts on it | Not started |

Sequencing follows the numbers above — each depends on the ones before it
(retrieval needs indexes populated by ingest, which needs the model layer
for embedding; orchestration needs retrieval; eval needs orchestration to
have something to evaluate).
