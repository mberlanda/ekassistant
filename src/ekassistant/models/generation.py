"""Cite-or-abstain answer generation.

See docs/design/model-layer.md#cite-or-abstain-contract. A response is only
ever returned as a real answer if every citation it makes points at a
chunk_id that was actually in the provided context - anything else
(malformed JSON, missing citations, an empty answer, invented chunk_ids)
is downgraded to an abstain, never presented as a best-effort answer.

Transport/API errors from the chat client (connection refused, model not
pulled, etc.) are intentionally NOT caught here and propagate to the
caller - silently turning an infrastructure failure into an abstain would
misrepresent "the system is down" as "the answer isn't in the documents".
"""

from typing import Protocol

from pydantic import ValidationError

from ekassistant.models.context import ContextChunk
from ekassistant.models.schema import Citation, GroundedAnswer, ModelResponse

SYSTEM_PROMPT = (
    "You are an enterprise knowledge assistant. Answer the user's question "
    "using ONLY the provided context chunks. Every claim in your answer must "
    "be supported by at least one cited chunk_id from the context. If the "
    "context does not contain enough information to answer, set abstained "
    "to true, leave answer empty, and cite nothing. Never invent a chunk_id "
    "that is not present in the provided context. Always also report a "
    "confidence score between 0.0 and 1.0 reflecting how well the provided "
    "context supports your answer (1.0 = fully and directly supported, "
    "0.0 = no support at all)."
)

# GroundedAnswer.reason values - see docs/design/observability.md's
# distinction between "abstain rate" and "citation-validation failure
# rate" as separate metrics.
REASON_EMPTY_CONTEXT = "empty_context"
REASON_MALFORMED_RESPONSE = "malformed_response"
REASON_MODEL_REPORTED_ABSTAIN = "model_reported_abstain"
REASON_BLANK_ANSWER = "blank_answer"
REASON_NO_CITATIONS = "no_citations"
REASON_INVALID_CITATION = "invalid_citation"

# Matches Ollama's own stock default. A lower default was tried first
# (see docs/roadmap.md) on the theory that less sampling randomness would
# make the cite-or-abstain judgment more repeatable - live testing across
# repeated real runs disproved that: at temperature 0.2 the same
# question/context succeeded 1/10 times, worse than 0.8's 3-6/10 across
# repeated samples (a wide range itself - this small model's run-to-run
# variance at ANY fixed temperature is large enough that no single value
# reliably "fixes" it). Kept at Ollama's default rather than guessing a
# different number without evidence for one; the real value of this knob
# is letting a caller experiment (see the TUI's `:temp`), not a claimed
# fix for model unreliability - that's tracked as a model-quality
# characteristic (ADR-0004), not something a temperature setting solves.
# Duplicated (not imported) from Settings.ollama_temperature's default:
# the model layer must have a sane default even for a caller (tests, the
# eval harness) that never constructs a Settings instance, and this layer
# intentionally doesn't depend on config/.
DEFAULT_TEMPERATURE = 0.8


def _abstain(reason: str) -> GroundedAnswer:
    # A fresh instance every call: GroundedAnswer.citations is a mutable
    # list, so a shared module-level instance would let one caller's
    # mutation corrupt every other abstain response.
    return GroundedAnswer(answer="", citations=[], abstained=True, reason=reason)


class ChatClient(Protocol):
    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str: ...


def _format_context(chunks: list[ContextChunk]) -> str:
    # NOTE: chunk text is untrusted (it comes from ingested documents, see
    # docs/design/ingest.md) and isn't escaped here. A crafted chunk could in
    # principle embed a fake "[<other-chunk-id>] (source: ...)" delimiter to
    # blur the boundary with a different, genuinely-valid chunk_id. This is
    # the same class of prompt-injection risk docs/design/model-layer.md
    # already defers to a V2 guardrail model, not something this function
    # can fully close - flagged here rather than silently assumed away.
    return "\n\n".join(f"[{c.chunk_id}] (source: {c.source})\n{c.text}" for c in chunks)


def generate_answer(
    question: str,
    context_chunks: list[ContextChunk],
    client: ChatClient,
    temperature: float = DEFAULT_TEMPERATURE,
) -> GroundedAnswer:
    if not context_chunks:
        return _abstain(REASON_EMPTY_CONTEXT)

    user_prompt = f"Context:\n{_format_context(context_chunks)}\n\nQuestion: {question}"
    raw = client.chat_json(
        SYSTEM_PROMPT, user_prompt, ModelResponse.model_json_schema(), temperature=temperature
    )

    try:
        candidate = ModelResponse.model_validate_json(raw)
    except ValidationError:
        return _abstain(REASON_MALFORMED_RESPONSE)

    return _validate_citations(candidate, context_chunks)


def _validate_citations(
    candidate: ModelResponse, context_chunks: list[ContextChunk]
) -> GroundedAnswer:
    if candidate.abstained:
        return _abstain(REASON_MODEL_REPORTED_ABSTAIN)
    if not candidate.answer.strip():
        # abstained=False with a blank answer is neither a real answer nor
        # a signaled abstain - treat it as the latter rather than passing
        # an empty "success" through to the caller.
        return _abstain(REASON_BLANK_ANSWER)
    if not candidate.citations:
        return _abstain(REASON_NO_CITATIONS)

    chunks_by_id = {c.chunk_id: c for c in context_chunks}
    if any(citation.chunk_id not in chunks_by_id for citation in candidate.citations):
        return _abstain(REASON_INVALID_CITATION)

    # Rebuild citations from the authoritative context chunk, never from
    # the model's own echoed `source` field: a chunk_id is validated above,
    # but a model (possibly steered by injected text in the context) could
    # otherwise pair a valid chunk_id with a fabricated source label.
    trusted_citations = [
        Citation(chunk_id=citation.chunk_id, source=chunks_by_id[citation.chunk_id].source)
        for citation in candidate.citations
    ]
    return GroundedAnswer(
        answer=candidate.answer,
        citations=trusted_citations,
        abstained=False,
        reason=None,
        confidence=candidate.confidence,
    )
