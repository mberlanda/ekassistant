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
from ekassistant.models.schema import Citation, GroundedAnswer

SYSTEM_PROMPT = (
    "You are an enterprise knowledge assistant. Answer the user's question "
    "using ONLY the provided context chunks. Every claim in your answer must "
    "be supported by at least one cited chunk_id from the context. If the "
    "context does not contain enough information to answer, set abstained "
    "to true, leave answer empty, and cite nothing. Never invent a chunk_id "
    "that is not present in the provided context."
)


def _abstain() -> GroundedAnswer:
    # A fresh instance every call: GroundedAnswer.citations is a mutable
    # list, so a shared module-level instance would let one caller's
    # mutation corrupt every other abstain response.
    return GroundedAnswer(answer="", citations=[], abstained=True)


class ChatClient(Protocol):
    def chat_json(self, system: str, user: str, json_schema: dict) -> str: ...


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
    question: str, context_chunks: list[ContextChunk], client: ChatClient
) -> GroundedAnswer:
    if not context_chunks:
        return _abstain()

    user_prompt = f"Context:\n{_format_context(context_chunks)}\n\nQuestion: {question}"
    raw = client.chat_json(SYSTEM_PROMPT, user_prompt, GroundedAnswer.model_json_schema())

    try:
        candidate = GroundedAnswer.model_validate_json(raw)
    except ValidationError:
        return _abstain()

    return _validate_citations(candidate, context_chunks)


def _validate_citations(
    candidate: GroundedAnswer, context_chunks: list[ContextChunk]
) -> GroundedAnswer:
    if candidate.abstained:
        return _abstain()
    if not candidate.answer.strip():
        # abstained=False with a blank answer is neither a real answer nor
        # a signaled abstain - treat it as the latter rather than passing
        # an empty "success" through to the caller.
        return _abstain()
    if not candidate.citations:
        return _abstain()

    chunks_by_id = {c.chunk_id: c for c in context_chunks}
    if any(citation.chunk_id not in chunks_by_id for citation in candidate.citations):
        return _abstain()

    # Rebuild citations from the authoritative context chunk, never from
    # the model's own echoed `source` field: a chunk_id is validated above,
    # but a model (possibly steered by injected text in the context) could
    # otherwise pair a valid chunk_id with a fabricated source label.
    trusted_citations = [
        Citation(chunk_id=citation.chunk_id, source=chunks_by_id[citation.chunk_id].source)
        for citation in candidate.citations
    ]
    return GroundedAnswer(answer=candidate.answer, citations=trusted_citations, abstained=False)
