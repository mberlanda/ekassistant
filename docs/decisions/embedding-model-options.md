# Embedding model options (reference for ADR-0006)

This is a decision-support reference, not itself a decision — it exists
because the project owner asked for the alternatives to be documented
before picking one, not to have a single unexplained model appear in
config. See [ADR-0006](0006-embedding-model-choice.md) for the actual
choice and its rationale.

An "embedding model" here turns a text chunk into a fixed-length vector for
dense/semantic search (the "dense" half of hybrid retrieval, see
[ADR-0003](0003-hybrid-retrieval-with-rrf.md)). Candidates below are
grouped by serving path, since that affects both download size and runtime
dependencies, not just retrieval quality.

## Constraints that matter for this choice

- **Trust boundary**: the brief requires embedding to happen "in trust
  boundary" — i.e. document text must not leave the local/controlled
  environment to be embedded by a third-party API. This rules out hosted
  embedding APIs for V1 outright, regardless of quality.
- **Bandwidth**: the developer's connection is currently poor; download
  size is a real cost, not a footnote.
- **Runtime footprint**: the project already depends on Ollama for the LLM
  (see [ADR-0004](0004-local-llm-serving-via-ollama.md)). Reusing it for
  embeddings avoids adding a second heavyweight runtime (PyTorch +
  `transformers`, generally several GB of dependencies) just to embed text.

## Candidates

| Model | Serving path | Download size | Embedding dimensions | Context window | Notes |
|---|---|---|---|---|---|
| `nomic-embed-text` | Ollama (local API) | ~274 MB | 768 | 8192 tokens | Purpose-built for retrieval; long context handles large chunks without truncation; no extra Python ML dependencies since Ollama already runs it |
| `mxbai-embed-large` | Ollama (local API) | ~670 MB | 1024 | 512 tokens | Stronger benchmark scores than `nomic-embed-text` on some retrieval leaderboards; bigger download; short context window means longer chunks must be split more aggressively |
| `BAAI/bge-small-en-v1.5` | `sentence-transformers` (Python, local) | ~133 MB (model weights) | 384 | 512 tokens | Strong quality-for-size reputation; requires adding PyTorch + `sentence-transformers` as dependencies (multi-GB install, separate from Ollama), which is a heavier *runtime* footprint than the download number alone suggests |
| `sentence-transformers/all-MiniLM-L6-v2` | `sentence-transformers` (Python, local) | ~90 MB (model weights) | 384 | 256 tokens (trained length) | Smallest, fastest, extremely well-established; same PyTorch dependency cost as `bge-small`; lower retrieval quality ceiling than the others here |
| Hosted embedding API (e.g. a cloud provider's embeddings endpoint) | Network call | 0 (no download) | varies | varies | No local compute or download cost at all — but sends internal document text to a third party, which directly violates the "embed in trust boundary" requirement. Ruled out for V1. |

## How to think about the tradeoff

Two axes actually matter for this decision, and they're independent:

1. **Retrieval quality** (recall/precision on the eventual eval harness —
   see [observability design](../design/observability.md)). None of the
   small candidates above are definitively "best" in the abstract; ranking
   shifts by benchmark and by corpus. This is only trustworthy once
   measured against this project's own eval set, not from leaderboard
   numbers alone.
2. **Runtime cost**: an Ollama-served model adds zero new dependencies
   (Ollama is already required for the LLM) and zero new download beyond
   the model weights themselves. A `sentence-transformers` model adds
   PyTorch and its dependency tree to the Python environment — on a slow
   connection, *that* install can cost more download time than the model
   weights do, even though the model file itself is smaller.

For a POC bootstrapping on a poor connection, axis 2 dominates: the
cheapest path to "embeddings work end-to-end" is staying inside the
runtime already being installed for the LLM.
