# Low-level design: Model Layer

Reference spine item e. Hosts every model call in the system: the answer
Large Language Model (LLM), the embedding model, and (V2+) a guardrail
model, all behind routing so callers depend on a stable interface, not a
specific model.

## Responsibilities

1. **LLM call**: given assembled, cited context and the user's question,
   produce a grounded answer that cites specific chunks, or an explicit
   abstain response when the context doesn't support an answer — see
   [cite-or-abstain contract](#cite-or-abstain-contract) below.
2. **Embedding call**: given text, return its vector representation, used
   both at ingest time (per chunk) and at query time (per question) —
   see [ADR-0006](../decisions/0006-embedding-model-choice.md).
3. **Structured output enforcement**: the LLM's response must be
   machine-parseable (answer text + a list of chunk IDs it cites), not
   free text with citations embedded ad hoc — parsed and validated before
   it's returned to the caller.
4. **Routing** (V2+): choosing between models (e.g. a faster/cheaper model
   for simple queries, a stronger one for complex ones). V1 has exactly one
   LLM and one embedding model configured, so routing is a single-target
   passthrough today, but the interface is shaped to add real routing
   later without changing callers.
5. **Guardrail model** (V2+, not implemented in V1): a smaller/cheaper
   model or classifier used to check inputs/outputs (e.g. detect an
   out-of-policy question) before or after the main LLM call. Named in the
   reference spine, deferred here since V1's cite-or-abstain contract on
   the main LLM already covers the core "don't answer what you can't
   ground" requirement.

## Cite-or-abstain contract

The LLM is prompted to return a structured result:

```
{
  "answer": "<text, or empty if abstaining>",
  "citations": [{"chunk_id": "...", "source": "..."}],
  "abstained": true | false,
  "confidence": <0.0-1.0, optional>
}
```

Validation applied after the model responds, before returning to the
caller:

- If `abstained` is `false`, every claim-bearing sentence in `answer` is
  expected to correspond to at least one entry in `citations`, and every
  cited `chunk_id` must be a chunk that was actually in the assembled
  context (never a model-invented ID).
- If validation fails (citations missing, or a citation points outside the
  provided context), the system treats the response as an abstain, not as
  a best-effort answer — silently downgrading a broken citation to "no
  citation" would defeat the grounding guarantee.

`confidence` is the model's own, unvalidated self-assessment of how well
the context supports its answer — passed through to the caller as-is on
a non-abstained answer, `null` on any abstain (including a
citation-validation downgrade: if the response wasn't trusted enough to
surface as an answer, its self-reported confidence about that answer
isn't trusted either). Deliberately **optional**, not a required part of
the schema: an earlier version made it required, and live testing found
that a 4th required field measurably pushed this small local model toward
abstaining more often (reproduced at 0/6 successful answers on a
previously-reliable question with it required, vs. 6/6 with it optional —
the model still voluntarily included it every time once the requirement
was lifted). See `ModelResponse.confidence`'s docstring in
`models/schema.py`.

Generation temperature is also caller-overridable (`POST /query`'s
optional `temperature` field, or the TUI's `:temp` command), defaulting
to `Settings.ollama_temperature` (see `models/generation.py`'s
`DEFAULT_TEMPERATURE`, which matches Ollama's own stock default). This
was added expecting a lower default to reduce the model's run-to-run
cite-or-abstain flakiness; repeated live testing disproved that
specifically (a lower default measured *worse*, and no fixed temperature
reliably fixed a noisy-context case), so the knob is kept as an
experimentation aid rather than tuned to a claimed-better default — see
`DEFAULT_TEMPERATURE`'s docstring for the actual numbers.

## Tradeoffs

- **Structured output validated in application code, not trusted blindly**:
  small local models (see [ADR-0004](../decisions/0004-local-llm-serving-via-ollama.md))
  are less reliable at strictly following an output schema than larger or
  hosted models. Validating and failing closed to "abstain" costs some
  answer coverage (valid answers occasionally get discarded over a
  formatting slip) in exchange for never presenting an ungrounded or
  mis-cited answer as if it were grounded — the latter is the one failure
  mode the brief treats as unacceptable.
- **One model layer module for LLM + embeddings, not two**: keeping both
  behind the same layer (even though V1 only routes each to one model)
  means the guardrail-model and multi-model-routing seams already exist
  when V2 needs them, at the cost of a small amount of unused flexibility
  today.
- **No prompt-injection-specific guardrail in V1**: retrieved document
  content is untrusted input (it comes from wikis/tickets/chats, not from
  the developer), and a malicious or careless document could contain text
  aimed at manipulating the LLM's instructions. V1 relies on the
  cite-or-abstain structural validation above as a partial mitigation
  (an injected instruction can't fabricate a valid citation into real
  context) but does not implement a dedicated guardrail/classifier pass —
  tracked as V2+ scope alongside the guardrail model itself.
