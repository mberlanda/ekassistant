# 0009. Parse HTML/PDF/DOCX via lightweight libraries; author fixtures instead of downloading a public corpus

Date: 2026-08-07

## Status

Accepted

## Context

Real internal sources (wikis, share drives, tickets) aren't a single file
format. [ADR-0002](0002-acl-enforcement-at-retrieval.md) and the ingest
core PR (roadmap item 3) already handle plain text/Markdown; extending
format coverage per [docs/design/ingest.md](../design/ingest.md) means two
separate decisions:

1. Which libraries parse HTML, PDF, and DOCX into the Markdown-ish,
   `#`-heading text the existing chunker already understands.
2. What content backs the new format-specific fixtures in the seed
   corpus - reuse/download a real public corpus, or author new samples.

## Decision

**Parsing**: `beautifulsoup4` for HTML (walk `<h1>`-`<h6>` and `<p>` tags
in document order, converting headings to `#`-prefixed lines), `pypdf` for
PDF (plain per-page text extraction, no heading synthesis - see
[tradeoffs](#tradeoffs)), `python-docx` for DOCX (paragraph style name
`"Heading N"` maps to `#`×N, everything else is a plain paragraph). Each
lives behind a shared `parse_document(path) -> str` dispatcher keyed on
file extension, so the connector and chunker never need to know which
format they're looking at.

**Fixtures**: one new hand-authored sample document per new format
(`engineering-onboarding.html`, `vendor-contract-terms.pdf`,
`customer-escalation-policy.docx`), each mapped to the same ACL groups as
an existing Markdown sample, rather than downloading a real-world public
corpus (e.g. a Wikipedia dump, an open enterprise-wiki export).

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Heavier "universal" extraction library (e.g. `unstructured`, `textract`) | One dependency instead of three; more format coverage out of the box (images, tables, more office formats) | Large dependency tree (often pulls in ML models/OCR stacks) for a POC that only needs 3 more formats; less control over exactly how headings map to the chunker's convention |
| `beautifulsoup4` + `pypdf` + `python-docx` (chosen) | Small, focused, pure-Python-or-near-it dependencies; each is the de facto standard single-purpose library for its format; explicit control over the heading-mapping convention | Three dependencies instead of one; no OCR (scanned/image-only PDFs extract no text - acceptable, out of scope for typed source documents) |
| Download a public corpus for the new fixtures | More "realistic" content and volume than anything hand-written | Adds licensing/attribution tracking for content this project doesn't otherwise need to manage; download size works against the same poor-connection constraint that shaped [ADR-0004](0004-local-llm-serving-via-ollama.md); and a downloaded corpus's real-world ACL boundaries (if any) wouldn't line up with the mock groups in `config/identities.yaml`, so it would need relabeling anyway |
| Hand-authored fixtures (chosen) | Zero licensing/provenance concerns (original content, same treatment as the existing Markdown fixtures); ACL groups can be assigned to match `config/identities.yaml` exactly, keeping the access-boundary demo precise; tiny, fast, no download | Small and synthetic - doesn't exercise parsing edge cases a large messy real-world corpus would (irregular formatting, embedded tables, mixed encodings) |

## Tradeoffs of the chosen option

PDF text extraction gets no heading structure: `pypdf`'s basic
`extract_text()` doesn't reliably expose font-size/weight as a heading
signal the way DOCX's paragraph styles or HTML's tag names do, so
synthesizing `#` markers from PDF would mean guessing from font metrics -
more complexity than this POC's PDF support is worth. The chunker's
heading-based splitting is simply a no-op on PDF output; it falls through
to paragraph/character-budget splitting instead ([see
`docs/design/ingest.md`](../design/ingest.md#tradeoffs) for that
fallback). This means PDF-sourced chunks are coarser-grained than
HTML/DOCX/Markdown ones from the same size of source document - an
accepted format-specific quality gap, not a bug.

The HTML parser only reads `<h1>`-`<h6>` and `<p>` - no lists, tables,
`<div>`-based layout text, or inline formatting. This is a deliberate
scope limit matching what "core format adapters" needs to prove the
pipeline works across formats, not a general HTML-to-Markdown converter.

Choosing hand-authored fixtures over a public corpus trades realism for
zero governance overhead. If/when retrieval quality needs evaluating
against messier real-world content, that's a separate, explicit future
decision - it should not be backed into by accumulating downloaded
content without a licensing review.

## Consequences

- Adding a new format later (e.g. `.pptx`, `.csv`) means adding one parser
  module and a `PARSERS` dict entry in
  `src/ekassistant/ingest/parsers/__init__.py` - the connector and
  chunker are unaffected.
- The seed corpus now has 7 documents across 4 formats, still small enough
  to hand-verify (as done for roadmap item 3) but wide enough to prove
  every format adapter's ACL and chunking behavior end to end.
- If retrieval quality evaluation (a later PR) shows PDF's coarser
  chunking meaningfully hurts answer quality, revisit PDF heading
  detection (e.g. font-size heuristics via `pypdf`'s layout mode) before
  reaching for a heavier extraction library.
