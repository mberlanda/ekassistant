# 0004. Serve the answer-generation LLM locally via Ollama, starting with a small model

Date: 2026-08-07

## Status

Accepted

## Context

The Model Layer (spine item e) needs a Large Language Model (LLM) to
compose grounded, cite-or-abstain answers (pipeline stage `e`). Two
constraints from the brief shape this decision directly:

- The developer's internet connection is currently poor, so whatever is
  downloaded first must be small and quick to fetch.
- Ollama is already installed locally, and the model choice should be easy
  to swap out later without changing application code.

Enterprise document content (support tickets, wiki pages, chat threads) may
also be sensitive, which favors keeping inference in-process/on-machine for
the POC rather than sending it to a third-party hosted API by default.

## Decision

Serve the LLM through **Ollama**, accessed via its local HTTP API
(`http://localhost:11434`), with the **model name as a config value**
(`OLLAMA_MODEL` in [settings](../../src/ekassistant/config/settings.py)),
never hardcoded — so upgrading the model later is a one-line config change,
not a code change.

For the initial download, pull **`llama3.2:1b`** (Meta, ~1.3 GB). If the
connection is too poor even for that, fall back to **`qwen2.5:0.5b`**
(Alibaba, ~398 MB) as a smaller first download, and upgrade once bandwidth
allows. Both are instruction-tuned and support the structured, cite-or-abstain
prompting style V1 needs; neither is expected to be the long-term production
choice.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Hosted LLM API (e.g. a cloud provider's completions endpoint) | No local download at all; generally higher quality than small local models | Requires a reliable connection for *every* query, not just a one-time download — worse fit for "internet connection is currently horrible"; sends potentially sensitive enterprise content to a third party by default, which needs its own governance sign-off before V1 |
| Ollama, larger local model (7B+ instruct class) | Meaningfully better answer quality/instruction-following out of the box | Multi-gigabyte download, directly conflicts with the stated bandwidth constraint; slower first-run inference on a laptop |
| Ollama, `llama3.2:1b` (chosen for first pull) | ~1.3 GB, quick even on a poor connection; instruction-tuned; well-supported in Ollama; good enough to validate the cite-or-abstain pipeline end-to-end | Noticeably weaker reasoning/instruction-following than larger models — expect to revisit once the pipeline works and bandwidth/time allow a bigger pull |
| Ollama, `qwen2.5:0.5b` (documented fallback) | Smallest reasonable instruct model (~398 MB); fastest possible path to an end-to-end smoke test | Weakest of the options on following the cite-or-abstain output contract; treat as a bootstrap-only choice, swap out as soon as `llama3.2:1b` (or better) is available |

## Tradeoffs of the chosen option

Small (≤3B parameter) local models are meaningfully worse than either
larger local models or hosted frontier APIs at following complex
instructions (like "cite every claim, and if the context doesn't support an
answer, say so instead of guessing"), and at general reasoning. For V1 this
is an accepted, temporary tradeoff: the goal right now is to validate the
*pipeline* (retrieval → grounding → citation → abstain), not to maximize
answer quality. Because the model is a config value, not a code dependency,
this tradeoff is cheap to revisit later without touching the orchestration
or prompting code.

Running inference locally also means the developer machine's CPU/GPU is
the only compute available — no elastic scaling, no concurrent-request
headroom. That's fine for a single-user POC and wrong for anything beyond
it.

## Consequences

- The prompt/output contract for the answer-generation step (structured
  citations, explicit abstain path) must be validated against whichever
  small model is actually pulled, since small models are less reliable at
  following format instructions — this is exactly what the eval harness
  (see [observability design](../design/observability.md)) exists to catch
  regressions on when the model is swapped later.
- Guardrail/classification tasks in the Model Layer (spine item e) may use
  a *different*, possibly even smaller, model than answer generation — that
  is a separate decision, not covered by this ADR.
- Model pulls (`ollama pull <name>`) are a manual, explicit step (see the
  [README quickstart](../../README.md)) rather than something automated on
  first run, since they are slow/network-dependent and shouldn't block
  unrelated work.
