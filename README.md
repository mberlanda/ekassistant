# Enterprise Knowledge Assistant

Employees ask questions in natural language and get grounded, cited answers
drawn from internal documents — wikis, share drives, customer support
tickets, chats — while respecting Access Control List (ACL) boundaries.

This README is the entry point. Every section below links to the document
that actually owns that topic — this file stays short on purpose.

## Start here

| If you want to know... | Go to |
|---|---|
| The original requirements, verbatim | [docs/context/brief.md](docs/context/brief.md) |
| How the whole system fits together (diagrams) | [docs/architecture.md](docs/architecture.md) |
| *Why* a specific decision was made | [docs/decisions/](docs/decisions/) (Architecture Decision Records) |
| How a specific component is built, with tradeoffs | [docs/design/](docs/design/) (low-level designs) |
| What an abbreviation means | [docs/glossary.md](docs/glossary.md) |
| What's actually built vs. still designed-only | [docs/roadmap.md](docs/roadmap.md) |

## Scope

**V1**: grounded question-answering over a few high-value internal sources,
with strict per-user access control and citations, abstaining when an
answer isn't found in retrieved context.

**Deferred**: write actions, cross-source reasoning (needs a knowledge
graph), agentic multi-hop retrieval. See
[docs/context/brief.md](docs/context/brief.md#deferred-explicitly-out-of-scope-for-v1).

## The two decisions that shape everything else

1. **Access control is enforced at retrieval**, as a pre-filter inside the
   index — not a post-hoc filter, not a UI-only check. See
   [ADR-0002](docs/decisions/0002-acl-enforcement-at-retrieval.md).
2. **Retrieval is hybrid**: dense (embeddings) + keyword (Best Matching 25,
   BM25), fused with Reciprocal Rank Fusion (RRF). See
   [ADR-0003](docs/decisions/0003-hybrid-retrieval-with-rrf.md).

## Repository map

```
config/
  identities.yaml         mock user -> group mapping (see ADR-0007)
  crawl_targets.yaml       web crawler seed URLs + ACL (empty by default, see ADR-0010)
docs/
  context/brief.md      original requirements (historical record)
  architecture.md        system-wide diagrams + spine-to-doc index
  decisions/              ADRs — the *why*
  design/                 low-level designs — the *how*, per component
  glossary.md            every abbreviation, defined once
  roadmap.md              what's built vs. designed-only, PR by PR
seed_corpus/               tiny hand-authored fixture corpus (.md/.html/.pdf/.docx)
src/ekassistant/          application code, one subpackage per component
  config/                 settings (env-driven)
  identity/               mock user -> group lookup
  ingest/                 connectors (filesystem, web crawler), parsing, chunking, embedding
  index/                  vector (Qdrant) + keyword (SQLite FTS5) adapters
  retrieval/               ACL pre-filter, hybrid search, RRF, rerank
  orchestration/          request pipeline (retrieve -> generate)
  models/                 LLM + embedding clients (Ollama-backed)
  api/                    API Gateway (FastAPI)
  tui/                    terminal client
  observability/         tracing, metrics, eval harness
  governance/             ACL-propagation delete checks
tests/
docker-compose.yml        Qdrant, run via OrbStack
Makefile                  venv, install, lint, test, up, down, api, tui, models, ingest, eval
```

## Quickstart

Requires: [pyenv](https://github.com/pyenv/pyenv), [Ollama](https://ollama.com)
(installed and running — the desktop app keeps its service up automatically;
CLI-only installs need `ollama serve` running in the background), and
[OrbStack](https://orbstack.dev) for the Docker runtime.

```bash
# 1. Interpreter + virtualenv (uses the pinned version in .python-version)
make venv
make install

# 2. Pull the small local models (~1.6 GB total; see ADR-0004 and ADR-0006
#    for why these specific models were picked for a poor-connection start)
make models

# 3. Bring up the vector store
make up

# 4. Ingest the seed corpus (+ any web crawler targets you've configured -
#    see config/crawl_targets.yaml, empty/no-op by default) into both indexes.
#    Re-running this is safe: it's idempotent, and removing a document/crawl
#    target and re-running propagates the deletion to both indexes - see the
#    "Governance" row in docs/roadmap.md.
make ingest

# 5. Run the API Gateway, then the TUI in another terminal
make api
make tui
```

In the TUI, `:user <id>` switches the mock identity (try `alice`, `bob`,
`carol`, or `guest` — see `config/identities.yaml`), then just type a
question. `alice` (engineering) asking "Does the VPN require multi-factor
authentication?" is the question exercised in
[docs/roadmap.md](docs/roadmap.md)'s live verification notes.

Config lives in `.env` (copy `.env.example`); mock users/groups live in
`config/identities.yaml` — see
[ADR-0007](docs/decisions/0007-mock-identity-and-group-lookup.md) for why
authentication is mocked in V1, and never treat it as a real security
boundary.

**Optional**: `make eval` runs the two-tier eval harness against the live
services — Tier 1 is deterministic ACL/retrieval correctness (every mock
user × every seed_corpus document), Tier 2 measures (doesn't assert)
generation quality, since the small local chat model is
[documented as unreliable](docs/decisions/0004-local-llm-serving-via-ollama.md)
at the cite-or-abstain contract by design, not by bug. Requires `make ingest`
to have run first.

## Status

V1 is complete end-to-end: grounded question-answering, per-user ACL
enforcement at retrieval, citations, abstain-when-not-found, a two-tier
eval harness, ACL-propagation delete checks, and a domain-agnostic web
crawler connector (opt-in via `config/crawl_targets.yaml`, empty by
default). See [docs/roadmap.md](docs/roadmap.md) for the PR-by-PR
breakdown, including what each PR's live verification actually proved.
