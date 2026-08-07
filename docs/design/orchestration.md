# Low-level design: Orchestration

Reference spine item c (and item d, Tool Layer, folded in here since V1 has
no real tools yet). Wires the [retrieval](retrieval.md) and
[model](model-layer.md) layers together into one request/response cycle.
See [ADR-0008](../decisions/0008-lightweight-orchestration.md) for why this
is hand-written rather than framework-based.

## Responsibilities

1. **State manager**: holds the current request's short-term (session)
   context — see [retrieval design](retrieval.md#short-term-vs-long-term-memory)
   — and passes it through each stage.
2. **Planner/router**: for V1, a fixed single path (rewrite → retrieve →
   generate); not a general planning loop. Named to match the reference
   spine's vocabulary and to mark where a real planner would slot in once
   multi-hop retrieval is in scope.
3. **Tool-caller / Tool Layer**: the interface a future "search a specific
   source", "look up a ticket by ID", etc. tool would implement. V1 ships
   this as an empty registry — retrieval itself is not modeled as a "tool"
   the LLM calls, it's a fixed pipeline stage, since V1 has no agentic
   loop deciding *whether* to retrieve.
4. **Approval gates**: a hook point for requiring human/policy approval
   before an action executes. Since write actions are deferred (see
   [the brief](../context/brief.md)), V1 has no action that needs gating —
   the seam exists (a no-op `require_approval()` call site) so V2 doesn't
   need to restructure the pipeline to add it.
5. **Request pipeline**: the concrete sequence —

```
1. Resolve identity -> group set        (from API Gateway & Identity, see api-gateway-identity.md)
2. Rewrite/route query                  (retrieval.md)
3. ACL-filtered hybrid search + rerank  (retrieval.md)
4. Assemble context                     (retrieval.md)
5. Generate cite-or-abstain answer      (model-layer.md)
6. Validate citations, log the run      (model-layer.md, observability.md)
7. Return answer + citations, or abstain
```

## Tradeoffs

- **Fixed pipeline vs. a planning loop**: a fixed sequence is fully
  predictable and easy to trace/test end to end, but cannot decide, e.g.,
  "this question needs two retrieval rounds" — that's exactly the agentic
  multi-hop capability the brief defers, so this tradeoff is intentional
  for V1 and should be revisited together with
  [ADR-0008](../decisions/0008-lightweight-orchestration.md) if that scope
  is picked up.
- **Empty tool registry now, not omitted entirely**: defining the Tool
  Layer's interface without populating it costs a small amount of
  unused-code surface today, in exchange for V2 adding tools (e.g. "fetch
  a live ticket status") as new registrations instead of a pipeline
  rewrite.
- **Approval gates as a no-op hook, not absent**: same shape of tradeoff —
  a hook that always allows is indistinguishable from no hook at runtime,
  but its presence in the code marks exactly where write-action approval
  needs to be inserted once write actions exist, rather than requiring
  that to be rediscovered later.
