"""Cite-or-abstain answer generation.

See docs/design/model-layer.md#cite-or-abstain-contract. A response is only
ever returned as a real answer if every citation it makes points at a
chunk_id that was actually in the provided context - anything else
(malformed JSON, missing citations, invented chunk_ids) is downgraded to
an abstain, never presented as a best-effort answer.
"""

from typing import Protocol

from pydantic import ValidationError

from ekassistant.models.context import ContextChunk
from ekassistant.models.schema import GroundedAnswer

SYSTEM_PROMPT = (
    "You are an enterprise knowledge assistant. Answer the user's question "
    "using ONLY the provided context chunks. Every claim in your answer must "
    "be supported by at least one cited chunk_id from the context. If the "
    "context does not contain enough information to answer, set abstained "
    "to true, leave answer empty, and cite nothing. Never invent a chunk_id "
    "that is not present in the provided context."
)

_ABSTAIN = GroundedAnswer(answer="", citations=[], abstained=True)


class ChatClient(Protocol):
    def chat_json(self, system: str, user: str, json_schema: dict) -> str: ...


def _format_context(chunks: list[ContextChunk]) -> str:
    return "\n\n".join(f"[{c.chunk_id}] (source: {c.source})\n{c.text}" for c in chunks)


def generate_answer(
    question: str, context_chunks: list[ContextChunk], client: ChatClient
) -> GroundedAnswer:
    if not context_chunks:
        return _ABSTAIN

    user_prompt = f"Context:\n{_format_context(context_chunks)}\n\nQuestion: {question}"
    raw = client.chat_json(SYSTEM_PROMPT, user_prompt, GroundedAnswer.model_json_schema())

    try:
        candidate = GroundedAnswer.model_validate_json(raw)
    except ValidationError:
        return _ABSTAIN

    return _validate_citations(candidate, context_chunks)


def _validate_citations(
    candidate: GroundedAnswer, context_chunks: list[ContextChunk]
) -> GroundedAnswer:
    if candidate.abstained:
        return _ABSTAIN

    valid_chunk_ids = {c.chunk_id for c in context_chunks}
    if not candidate.citations:
        return _ABSTAIN
    if any(citation.chunk_id not in valid_chunk_ids for citation in candidate.citations):
        return _ABSTAIN

    return candidate
