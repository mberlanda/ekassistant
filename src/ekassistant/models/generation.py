"""Cite-or-abstain answer generation.

See docs/design/model-layer.md#cite-or-abstain-contract. A response is only
ever returned as a real answer if every citation it makes points at a chunk
that was actually in the provided context - anything else (malformed JSON,
missing citations, an empty answer, out-of-range citation indexes) is
downgraded to an abstain, never presented as a best-effort answer.

The model cites chunks by a 1-based surrogate index rendered by
_format_context, not by their real chunk_id; see ModelCitation in schema.py
for why that indirection exists.

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
    "using ONLY the provided context chunks. Each chunk is introduced by a "
    "number in square brackets, e.g. '[2]'. Every claim in your answer must "
    "be supported by at least one cited chunk, and you cite a chunk by that "
    "bracketed number and nothing else - never by URL, filename, or any "
    "identifier that appears inside the chunk's own text. If the context "
    "does not contain enough information to answer, set abstained to true, "
    "leave answer empty, and cite nothing. Never cite a number that was not "
    "shown in the context. Always also report a confidence score between "
    "0.0 and 1.0 reflecting how well the provided context supports your "
    "answer (1.0 = fully and directly supported, 0.0 = no support at all)."
)

# TESTED AND REJECTED - do not re-add without measuring at n>=10:
#
#   - Repeating the cite-by-number instruction at the end of the user
#     message (a recency nudge, on top of SYSTEM_PROMPT). It looked like a
#     win at n=3 (2/3 vs 0/3 for granite4.1:8b) and that reading was pure
#     noise: at n=10 it left granite unchanged (1/10 either way) and was
#     actively destructive for llama3.2:1b, dropping it from 6/10 to 0/10.
#   - Ordering `citations` before `answer` in the schema, so constrained
#     decoding forces evidence selection before prose. No effect (0-1/10).
#   - Dropping the "(source: ...)" label from rendered chunks, and
#     re-labelling them "CHUNK n" instead of "[n]". Both left granite at
#     0/3 - the bracketed "[n]" rendering in _format_context is load-
#     bearing and shouldn't be "tidied".

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
    # Chunks are labelled with a 1-based surrogate index, NOT their real
    # chunk_id, and that index is what the model cites back (see
    # ModelCitation). The real chunk_id never has to survive a round trip
    # through the model at all.
    #
    # NOTE: chunk text is untrusted (it comes from ingested documents, see
    # docs/design/ingest.md) and isn't escaped here. A crafted chunk could in
    # principle embed a fake "[2] (source: ...)" delimiter to blur the
    # boundary with a different, genuinely-valid chunk. This is the same
    # class of prompt-injection risk docs/design/model-layer.md already
    # defers to a V2 guardrail model, not something this function can fully
    # close - flagged here rather than silently assumed away. Note the blast
    # radius is bounded: an injected index can only ever redirect a citation
    # to another chunk the caller was already authorized to retrieve, since
    # the index is resolved against this same ACL-filtered list.
    return "\n\n".join(
        f"[{position}] (source: {chunk.source})\n{chunk.text}"
        for position, chunk in enumerate(chunks, start=1)
    )


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

    # Citations arrive as 1-based surrogate indexes into context_chunks (the
    # same numbering _format_context rendered), so an out-of-range index is
    # the equivalent of the old "invented chunk_id" case. Field validation
    # already rejects index < 1, leaving only the upper bound to check here.
    if any(citation.index > len(context_chunks) for citation in candidate.citations):
        return _abstain(REASON_INVALID_CITATION)

    # Resolve each index against the authoritative context chunk. Both the
    # chunk_id and the source are taken from our own retrieved chunk, never
    # from model output - the model no longer supplies either, so a response
    # steered by injected context cannot pair a real chunk with a fabricated
    # label the way it could when it echoed `source` back to us.
    trusted_citations = [
        Citation(
            chunk_id=context_chunks[citation.index - 1].chunk_id,
            source=context_chunks[citation.index - 1].source,
        )
        for citation in candidate.citations
    ]
    return GroundedAnswer(
        answer=candidate.answer,
        citations=trusted_citations,
        abstained=False,
        reason=None,
        confidence=candidate.confidence,
    )
