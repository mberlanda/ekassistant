# 0003. Hybrid retrieval (dense + BM25) combined with Reciprocal Rank Fusion (RRF)

Date: 2026-08-07

## Status

Accepted

## Context

Dense (embedding) search alone is good at semantic/paraphrase matches but
weak at exact terms that matter a lot in enterprise text: ticket IDs,
error codes, product SKUs, acronyms, people's names. Keyword search (Best
Matching 25, BM25) is the opposite: excellent at exact-term recall, weak at
paraphrase and synonymy. The brief calls this out explicitly as a named key
decision: "retrieval quality with hybrid dense-plus-keyword with Reciprocal
Rank Fusion (RRF)".

Given two ranked lists (dense hits, keyword hits) over possibly different
scoring scales, they must be combined into one ranked candidate set before
reranking (pipeline stage `d` in the [brief](../context/brief.md)).

## Decision

Run dense search and BM25 keyword search **independently** (both already
scoped to the ACL pre-filtered candidate set — see
[ADR-0002](0002-acl-enforcement-at-retrieval.md)), then merge the two
ranked lists using Reciprocal Rank Fusion:

```
rrf_score(doc) = sum over each ranked list L containing doc of  1 / (k + rank_L(doc))
```

with `k = 60` (the standard default from the original RRF paper, chosen as
a starting point, tunable later against eval data). The fused list feeds
the cross-encoder reranker.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Dense search only | Simplest; one index type | Fails on exact-identifier queries ("ticket INC-4471"), which are common in enterprise support content |
| BM25 only | Simplest; cheap; great at exact terms | Fails on paraphrase/semantic queries ("how do I get reimbursed" vs. a doc titled "Expense policy") |
| Weighted linear combination of raw scores (`α · dense_score + (1-α) · bm25_score`) | Can be tuned per-corpus | Dense cosine/dot-product scores and BM25 scores live on incomparable, corpus-dependent scales; requires score normalization that's brittle across sources and re-tuning as content changes |
| Reciprocal Rank Fusion (chosen) | Scale-free — only uses rank position, not raw score, so no normalization needed; simple, well-studied, one hyperparameter (`k`) with a known-good default; works even when one retriever returns zero results | Discards score *magnitude* information (a very strong dense match and a barely-above-threshold one rank the same if both are #1); still needs `k` picked/tuned eventually |

## Tradeoffs of the chosen option

RRF is a rank-position heuristic, not a learned or calibrated combination —
it is a deliberately simple starting point that trades away the extra
precision a tuned/learned fusion (or a trained reranker doing this job
directly) could offer, in exchange for zero normalization work and
robustness to scale differences between retrievers. Because the reranker
(cross-encoder) sits immediately downstream and re-scores the fused
candidates directly against the query, RRF only needs to be "good enough"
to get the right candidates into the reranker's window — it does not need
to produce the final ranking itself.

## Consequences

- The retriever must request top-N (N > final top-k) from *each* of dense
  and keyword search independently before fusing, so the fused list has
  enough headroom for the reranker to have real candidates to re-order.
- `k=60` and the pre-reranker candidate window size are both parameters
  worth capturing in the eval harness (see
  [observability design](../design/observability.md)) so they can be tuned
  against measured retrieval quality rather than guessed twice.
- If eval data later shows RRF underperforming (e.g. one retriever is
  consistently more reliable for this corpus), revisit this ADR before
  reaching for a learned fusion model — that's added complexity this POC
  explicitly defers.
