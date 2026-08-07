# Low-level design: Retrieval / Memory

Pipeline stages `b` and `d` in [the brief](../context/brief.md); reference
spine item f. Given a user's question and their resolved group set, returns
a ranked, Access Control List (ACL)-safe, reranked set of chunks to ground
the answer in.

## Responsibilities

1. **Query rewrite / route**: normalize the raw question (and, in later
   versions, decide which source(s) or retrieval strategy it should route
   to — a no-op passthrough in V1 since only a few sources exist).
2. **ACL pre-filter**: compile the caller's resolved group set into a
   metadata filter applied *by* the vector and keyword indexes themselves,
   before ranking — see [ADR-0002](../decisions/0002-acl-enforcement-at-retrieval.md).
   This is not a separate step the retriever performs after search; it is
   an argument passed into both searches.
3. **Hybrid search**: run dense (vector) search and Best Matching 25
   (BM25) keyword search independently, each already ACL-filtered, each
   returning its own top-N ranked list.
4. **Fuse**: combine the two ranked lists with Reciprocal Rank Fusion
   (RRF) — see [ADR-0003](../decisions/0003-hybrid-retrieval-with-rrf.md).
5. **Rerank**: re-score the fused candidate window with a cross-encoder
   model against the actual query text (a cross-encoder scores a
   [query, candidate] pair jointly, which is more accurate but far more
   expensive per-pair than the bi-encoder/embedding similarity used for
   the initial dense search — hence only running it over a small
   post-fusion window, not the whole corpus).
6. **Assemble context**: take the top-k reranked chunks and format them
   (with source/citation metadata attached) into the context block handed
   to the [model layer](model-layer.md).

## Component boundaries

```
Query --> Rewriter --> ACL filter compiler --+--> Dense search  (Qdrant)  --+
                                              |                              |--> RRF fuse --> Reranker --> Context assembler
                                              +--> Keyword search (FTS5)  --+
```

Both `Dense search` and `Keyword search` are implemented behind small
ports (`search(query, acl_filter, top_n) -> RankedList`) so the concrete
engine behind each (see [ADR-0005](../decisions/0005-vector-and-keyword-store-choice.md))
can be swapped without touching the fusion/rerank/assembly code.

## Short-term vs. long-term memory

- **Short-term (session) memory**: the current conversation's turns, kept
  in-process for the life of a session, used to resolve follow-up
  questions ("what about last quarter?"). Not persisted in V1.
- **Long-term (user) memory**: durable, cross-session facts about a user's
  preferences or context. Explicitly **not implemented in V1** — the brief
  scopes V1 to single-turn grounded question-answering; long-term memory
  is deferred alongside agentic multi-hop retrieval, since it mainly pays
  off once multi-turn, multi-step interactions are in scope.

## Tradeoffs

- **Reranking a fused window, not the full candidate set**: a
  cross-encoder scores each [query, chunk] pair jointly through the model,
  which is far more accurate than comparing precomputed embeddings but
  cannot be precomputed or approximated the way vector search can — it
  must run at query time, on demand. Running it over, say, 150 fused
  candidates instead of every ACL-visible chunk trades a small chance of
  missing a good candidate that ranked poorly in *both* dense and keyword
  search for keeping query latency reasonable on local hardware
  (see [ADR-0004](../decisions/0004-local-llm-serving-via-ollama.md) for
  the same local-compute constraint applied to the LLM).
- **Reranker is a deferred/optional step for the earliest bootstrap**:
  given the poor-bandwidth constraint driving early model choices, the
  reranker (another model download) is wired as an interface from day one
  but may be a pass-through (identity reranker: keep RRF order) until a
  small cross-encoder model is pulled — this keeps the pipeline runnable
  end-to-end before every model is downloaded.
- **No query expansion/decomposition in V1**: the "query rewrite / route"
  step is intentionally close to a no-op for V1 (light normalization only)
  rather than LLM-driven query rewriting or decomposition into sub-queries
  — that shades into the agentic multi-hop retrieval the brief defers.
