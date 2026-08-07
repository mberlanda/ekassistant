# 0006. Use `nomic-embed-text` (via Ollama) as the default embedding model

Date: 2026-08-07

## Status

Accepted

## Context

See [embedding-model-options.md](embedding-model-options.md) for the full
comparison this decision is drawn from. In short: embeddings must be
computed inside the trust boundary (no hosted embedding API for V1), the
connection is currently poor, and Ollama is already required for the LLM
(see [ADR-0004](0004-local-llm-serving-via-ollama.md)).

## Decision

Use **`nomic-embed-text`**, served locally through Ollama, as the default
embedding model. Like the LLM choice, the model name is a config value
(`OLLAMA_EMBED_MODEL`), not hardcoded, so it can be swapped later without
touching ingest or retrieval code.

## Alternatives considered

Full comparison table in [embedding-model-options.md](embedding-model-options.md).
Summary of why the other three were not chosen:

| Option | Why not chosen (for now) |
|---|---|
| `mxbai-embed-large` (Ollama) | Larger download (~670 MB vs ~274 MB) and a much shorter context window (512 vs 8192 tokens), which forces smaller/more numerous chunks; plausible upgrade later if eval data shows it materially outperforms `nomic-embed-text` on this corpus |
| `BAAI/bge-small-en-v1.5` (`sentence-transformers`) | Requires adding PyTorch + `sentence-transformers` as new dependencies — a multi-gigabyte install on top of the model weights, which costs more download time on a poor connection than the model file size suggests |
| `sentence-transformers/all-MiniLM-L6-v2` | Same new-dependency cost as `bge-small`, plus a lower retrieval quality ceiling and a short 256-token training context |

## Tradeoffs of the chosen option

`nomic-embed-text` is not benchmarked as the top-quality option among the
four candidates — that distinction plausibly goes to `bge-small-en-v1.5` or
`mxbai-embed-large` depending on the benchmark. It is chosen for **fit with
the current constraints** (bandwidth, single-runtime dependency footprint)
over maximizing raw retrieval quality on paper. Its 8192-token context
window is generous for this project's chunk sizes (see
[ingest design](../design/ingest.md)), which is a genuine advantage, not
just a consolation.

## Consequences

- No new Python ML dependency (PyTorch, `sentence-transformers`) is
  required for V1; the embedding call is an HTTP request to the same
  Ollama instance already running for the LLM.
- The vector index (Qdrant, see [ADR-0005](0005-vector-and-keyword-store-choice.md))
  must be configured for 768-dimensional vectors to match this model's
  output; changing the embedding model later requires re-embedding the
  entire corpus and recreating the Qdrant collection at the new
  dimensionality — this is a real migration cost worth remembering before
  swapping models casually.
- If eval data (see [observability design](../design/observability.md))
  shows retrieval quality is the bottleneck rather than pipeline
  correctness, revisit this ADR against `mxbai-embed-large` first (same
  serving path, no new dependencies) before considering a
  `sentence-transformers` model.
