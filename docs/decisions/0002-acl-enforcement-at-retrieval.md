# 0002. Enforce Access Control Lists (ACLs) as a pre-filter at retrieval time

Date: 2026-08-07

## Status

Accepted

## Context

Source documents (wikis, share drives, customer support tickets, chats)
carry different Access Control Lists (ACLs): which groups may see which
document. The system must never let a user retrieve, or have an answer
grounded in, a document their identity's groups do not have access to —
this is the single hardest security requirement in the brief ("respecting
ACL boundaries" / "strict per-user access control").

The open question is *where in the pipeline* that boundary is enforced:
at the UI, in the orchestration layer, or inside the retrieval step
itself, and whether filtering happens *before* or *after* the similarity
search runs.

## Decision

ACLs are enforced as a **pre-filter inside the retrieval step**, immediately
before the hybrid search (dense + keyword) runs — see pipeline stage `d` in
the [brief](../context/brief.md). Every chunk is written to the vector and
keyword indexes with its source ACL as metadata (allowed group IDs). At
query time, the caller's identity is resolved to its group memberships (via
the mock lookup table, see [ADR-0007](0007-mock-identity-and-group-lookup.md))
and that group set is compiled into a **metadata filter that is applied by
the index itself as part of the search**, not by the application after
results come back.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Post-filter: run similarity search unfiltered, then drop disallowed results in application code | Simple to implement; works with any index | Information disclosure side channel — the *existence*, *count*, and *relative relevance* of documents a user cannot see leaks through the shape of the (filtered) result set, e.g. "top-k came back mostly empty" or timing differences; also wastes ranking budget/top-k slots on results that get thrown away, which can starve legitimate results out of the top-k entirely |
| Enforce ACLs only at the UI/answer-rendering layer | Minimal retrieval-layer changes | Retrieval and the LLM would still see (and could still ground/cite) unauthorized content before the UI redacts it — unacceptable, since the LLM call and any logs/traces already constitute exposure |
| Enforce ACLs only in the orchestration layer, by re-checking each retrieved document's ACL against the user before assembling context | Centralizes the check in one place | Same top-k starvation problem as post-filter, plus duplicates logic that the index can already do more efficiently as a native filter |
| Pre-filter at the index (chosen) | No unauthorized content ever enters the candidate set the ranker scores, so top-k is spent entirely on documents the user is actually allowed to see; no side channel; the index (Qdrant) is built to do this filtering efficiently alongside vector search | Requires every index (vector and keyword) to store and filter on ACL metadata consistently; ACL propagation/staleness becomes a first-class concern (see [ADR-0001 in governance design](../design/governance.md)) |

## Tradeoffs of the chosen option

- **Consistency burden**: both the vector store and the keyword store must
  carry the same ACL metadata schema and be kept in sync on every
  ingest/update/delete; a bug in either propagation path silently becomes an
  authorization bug, not just a relevance bug. This makes ACL propagation
  correctness one of the highest-priority things to test and observe (see
  [observability design](../design/observability.md)).
- **No graceful degrees**: pre-filtering is binary (visible or not) per
  chunk. It does not by itself support finer-grained policies like
  redaction-within-a-document; that would require chunk-level ACLs, which
  V1 supports at the chunk level already, so field/paragraph-level redaction
  remains future scope if ever needed.
- **Group membership must be resolved before retrieval starts**, coupling
  the query path to the identity lookup being fast and available; if it's
  slow or down, retrieval cannot safely proceed with a "fail open" default.
  The default must be fail-closed (abstain / error, never "search
  unfiltered").

## Consequences

- Every connector in the ingest layer must attach ACL metadata at write
  time; "no ACL metadata" must be treated as "no access", not "public",
  i.e. fail closed by default.
- The retriever component takes the resolved group set as a required input,
  not an optional one — there is no code path that calls the index without
  it.
- This decision is exercised end-to-end by the eval harness (see
  [observability design](../design/observability.md)): access-control test
  cases (a user asking about a document outside their groups) are treated
  as correctness tests, not just security tests.
