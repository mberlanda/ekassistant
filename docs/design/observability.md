# Low-level design: Observability

Reference spine item h. How the system's behavior is made legible: tracing,
metrics, evaluation, cost attribution, and prompt/version tracking.

## Responsibilities

1. **Tracing per run**: every query gets one trace spanning identity
   resolution → rewrite → retrieval (dense hits, keyword hits, fused
   ranking, reranked ranking) → generation → citation validation, so any
   answer (or abstain) can be explained after the fact — which chunks were
   candidates, which were ACL-filtered out, which were retrieved, which
   were cited.
2. **Metrics**: latency per pipeline stage, retrieval hit counts,
   abstain rate, citation-validation failure rate. Abstain rate in
   particular is a first-class metric here, not just an error case — a
   rising abstain rate can mean either "the system is correctly refusing
   to guess" or "retrieval quality regressed," and distinguishing those
   needs the eval harness below, not metrics alone.
3. **Eval harness**: a fixed set of labeled test questions (including
   access-control test cases — see
   [ADR-0002](../decisions/0002-acl-enforcement-at-retrieval.md)) run
   against the pipeline to score retrieval quality (did the right chunks
   come back) and answer quality (is the answer grounded, correctly cited,
   correctly abstained when it should be) — the mechanism referenced
   throughout the ADRs as "what would let us tune RRF's `k`, compare
   embedding models, or catch a regression from swapping the LLM."
4. **Cost attribution**: per-query resource accounting — for V1 with local
   models this is compute time rather than a metered API cost, but the
   same accounting hooks are what a hosted-model deployment would use to
   attribute real dollar cost per query/user.
5. **Prompt/version registry**: every prompt template and model
   name/version used in a run is recorded as part of that run's trace, so
   "which prompt produced this answer" is always answerable — required for
   debugging citation-validation failures and for evaluating prompt
   changes against the eval harness rather than by feel.

## Tradeoffs

- **Access-control cases as eval cases, not just security tests**:
  treating "does this user get an abstain/appropriately-filtered result
  for a document outside their groups" as a first-class row in the same
  eval harness as answer-quality cases means ACL regressions get caught by
  the same "run the eval suite" habit that catches quality regressions,
  rather than depending on a separate, easy-to-forget security test suite.
- **Trace everything, redact later vs. redact before tracing**: V1 traces
  full retrieved-chunk content for debuggability (essential for a POC
  that's actively being tuned), which means the trace store itself
  inherits the same ACL sensitivity as the documents it references. This
  is workable while traces stay local and developer-only; it is a gap
  flagged here explicitly, because it changes once traces are shared
  more broadly or move to a managed observability backend — at that point
  trace content needs the same access controls as the underlying
  documents, not looser ones.
- **Local, file-based observability for V1** (traces/metrics written to
  local files or a local lightweight store) instead of adopting a full
  tracing backend: keeps the dependency footprint down for a solo POC, at
  the cost of no built-in dashboarding/alerting — acceptable until the
  system has more than one user actively relying on it.
