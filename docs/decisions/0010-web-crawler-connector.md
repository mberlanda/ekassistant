# 0010. Domain-agnostic web crawler connector, config-driven ACL, two crawl modes

Date: 2026-08-08

## Status

Accepted

## Context

[docs/design/ingest.md](../design/ingest.md)'s Responsibilities name a
crawler connector as in-scope ("robots.txt-respecting, allowlisted domains
only"), and [docs/roadmap.md](../roadmap.md) item 5 tracked it as
deliberately deferred: earlier in this project, picking real target
domains was judged to be the project owner's call, not something to
default on, so the item was skipped rather than hardcoding a placeholder
site.

That blocker turns out to be about *configuration*, not *code*: nothing
about the connector itself needs to know which domains it will ever be
pointed at. This ADR covers the part that can be decided now - the
connector's shape, safety controls, and how it determines ACL for content
that (unlike every other source) carries no group-membership metadata of
its own - while leaving the actual domain list as empty, runtime-supplied
config, same as every other connector's manifest.

## Decision

**Connector shape**: `WebCrawlerConnector` implements the same
`DocumentSource` protocol (`load_documents() -> list[Document]`) as
`FilesystemConnector` (see [ingest.md](../design/ingest.md#component-boundaries)),
so it's a drop-in second connector for `run_ingest` - no changes needed to
chunking, embedding, indexing, or delete-propagation
([ADR](0005-vector-and-keyword-store-choice.md), governance checks). It
reuses `html_parser.py`'s existing structural extraction (`<h1>`-`<h6>`,
`<p>` in document order → the same `#`-heading convention every parser
already produces), refactored to accept an HTML string directly instead
of only a file path, since crawled content arrives over HTTP, not from
disk.

**Config-driven ACL, zero domains baked in**: a new `config/crawl_targets.yaml`
(default: empty list) lists targets the same way `seed_corpus/manifest.yaml`
lists local files - each entry pairs a seed URL with its `allowed_groups`.
An unmapped domain is never fetched at all, matching
[ADR-0002](0002-acl-enforcement-at-retrieval.md)'s fail-closed rule: no
ACL metadata means no access, not public access. Whoever deploys this
picks the domains; the code has no opinion.

**Two crawl modes per target**, chosen per entry, not globally:

- `single_page` (default): fetches exactly the seed URL. No links are
  ever followed. This is the safe default - functionally a "one URL is
  one document" ingest, with the same guarantees as fetching a local file.
- `same_origin` (opt-in): BFS-crawls links from the seed URL, bounded by
  a required `max_depth` and `max_pages`, restricted to the seed URL's
  exact scheme+host+port (redirects or links to a different host/subdomain
  are never followed, even nominally "related" ones - crossing an origin
  boundary is exactly the kind of scope creep an allowlist exists to
  prevent).

**robots.txt is always checked**, for both modes, before any fetch -
disallowed URLs are skipped, not overridden. A `User-Agent` string
identifies the crawler by name so a site operator can see what fetched
their content and block it via robots.txt if they want to.

**Fetch failures are non-fatal**: a single URL that times out, 404s, or
fails to parse is skipped (with a warning), not a run-aborting error - the
rest of the target (and every other document source) still ingests.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Hardcode a specific target domain now (the original, deferred approach) | Concrete, testable against one real site immediately | Requires the project owner to pick a real domain up front, which was explicitly deferred; ties the connector's first implementation to one site's HTML quirks |
| Single default group for all crawled content (e.g. "public"/"all-staff") | Simplest - no per-target config needed beyond a URL list | Wrong the moment two crawled sources have different real-world sensitivity (an internal wiki mirror vs. a public docs site); silently over-grants access the moment that happens |
| Config-driven per-target ACL mapping (chosen) | Matches every other source's ACL model (explicit, fail-closed, human-decided); one crawled target can be `engineering`-only while another is `all-staff` | One more config file to maintain; a target with no entry is silently never crawled rather than crawled-then-rejected (chosen deliberately - see [Tradeoffs](#tradeoffs-of-the-chosen-option)) |
| Same-origin crawling as the only mode | Simpler mental model, one code path | Forces every target (even a single specific page someone wants ingested) to carry depth/page-count config it doesn't need, and makes "just this one page" harder to express safely |
| `single_page` and `same_origin` as two modes (chosen) | `single_page` covers the common "ingest this one page" case with zero extra config or crawl risk; `same_origin` is available but requires explicit opt-in and bounds | Two code paths instead of one; a target's author must understand the distinction |
| Unrestricted crawling within robots.txt only, no allowlist/origin boundary | Most "crawler-like," follows the web as far as robots.txt allows | Directly contradicts ingest.md's "allowlisted domains only" scoping; a single misconfigured seed page (e.g. one with an outbound link to an unrelated site) could pull unrelated, unvetted, unclassified content into the index |

## Tradeoffs of the chosen option

- **No global default ACL**: every target needs an explicit, human-written
  `allowed_groups` entry. This is more setup than "just crawl everything
  and sort it out later," but it's the same tradeoff every other connector
  in this project already makes (ADR-0002's fail-closed philosophy applied
  consistently, not relaxed just because HTML lacks native ACL metadata).
- **`same_origin` mode's depth/page bounds are hard caps, not heuristics**:
  a genuinely large same-origin site with useful content past the cap
  simply won't be fully ingested. Safer than an unbounded crawl, but means
  `max_depth`/`max_pages` need occasional manual tuning per target rather
  than the crawler "figuring out" when it's done.
- **HTML structure extraction is inherited, not extended**: same scope
  limit as [ADR-0009](0009-ingest-format-adapters.md) - only `<h1>`-`<h6>`
  and `<p>` are read; lists, tables, and `<div>`-based layout text are
  dropped. A real docs/wiki site that leans on `<ul>`/`<table>` for
  meaningful content will lose that content on crawl, same as it would on
  a locally-ingested HTML file today.
- **No JavaScript rendering**: pages that require client-side rendering to
  produce their real content (SPA-style docs sites) will crawl as
  near-empty. Fine for typical server-rendered wiki/docs content; a real
  gap for JS-heavy sites, deferred rather than pulling in a headless
  browser dependency for a POC.

## Amendment (2026-08-08): URL and content dedup

Live-testing this connector against a real `same_origin` target
(`mauroberlanda.substack.com`) found several posts indexed twice: once
under a bare URL, once under a `?utm_source=...`-tagged variant of the
same link (blog platforms commonly tag their own internal/newsletter
links this way). Each copy consumed a separate slot in retrieval's fixed
top-k, wasting it on duplicate content instead of distinct documents.
This is a bug fix to the connector decided above, not a new architectural
decision, so it's recorded here rather than as a new ADR:

- **URL normalization now strips a small, known denylist of
  tracking/attribution query params** (`utm_*`, `fbclid`, `gclid`,
  `msclkid`, `mc_cid`/`mc_eid`, `igshid`, `ref_src`) in addition to the
  fragment-stripping this ADR already covered, so a tracking-tagged link
  variant of a page normalizes to the same URL as its bare form and
  dedupes before ever being double-fetched. Deliberately a narrow
  denylist, not "strip every query param" - an unrecognized param may be
  load-bearing (e.g. genuine pagination or an article ID), and
  over-stripping could wrongly merge two actually-different pages.
- **A content-based dedup safety net** (`_dedup_by_content`) runs once
  after all configured targets finish loading: if two documents (from the
  same target or different targets) have byte-identical extracted text,
  the first-encountered copy is kept and the rest are dropped, recorded
  in `skipped` with reason `"duplicate content of <url>"` rather than
  silently discarded. This catches duplicates URL normalization can't -
  e.g. two independently configured targets that converge on the same
  canonical content via URLs sharing no common structure.

This doesn't change the Decision, Alternatives, or Tradeoffs above - the
connector's shape, ACL model, and crawl modes are unchanged. It only
tightens what counts as "the same document" during ingestion.

## Consequences

- `docs/design/ingest.md` gains a crawler subsection describing this
  connector's Component boundary alongside the filesystem one.
- `config/crawl_targets.yaml` ships empty (a documented, zero-domain
  default) - nothing is crawled out of the box, matching this ADR's
  "the code has no opinion on domains" decision. The first real use
  requires someone to add an entry.
- `Document` (previously defined inside `connectors/filesystem.py`) moves
  to a shared `connectors/document.py` so both connectors depend on one
  definition instead of the crawler importing from the filesystem module.
- If a real target later needs JavaScript rendering or list/table content,
  that's a follow-up decision (a headless-browser dependency, or extending
  `html_parser.py`'s tag coverage) - not implied by this ADR.
