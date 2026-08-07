"""Fixed request pipeline: identity -> retrieve -> generate -> answer.

See docs/design/orchestration.md. Approval gates and the tool layer stay
doc-level seams only (see ADR-0008's consequences) rather than literal
empty-registry code, since V1 has no write action or tool call to gate -
this function genuinely is the entire pipeline for now.
"""

import time
from dataclasses import dataclass

from ekassistant.models.embedder import Embedder
from ekassistant.models.generation import ChatClient, generate_answer
from ekassistant.models.schema import GroundedAnswer
from ekassistant.retrieval.reranker import Reranker
from ekassistant.retrieval.retriever import KeywordSearcher, VectorSearcher, retrieve


@dataclass(frozen=True)
class PipelineResult:
    """Answer plus the per-run facts docs/design/observability.md asks for
    (retrieval hit count, per-stage latency) - returned alongside the
    answer rather than logged from inside this function, so pipeline.py
    stays free of any actual logging/storage concern; callers (the API
    Gateway) decide what to do with it.
    """

    answer: GroundedAnswer
    retrieved_chunk_ids: list[str]
    retrieval_ms: float
    generation_ms: float


def answer_question(
    question: str,
    allowed_groups: list[str],
    embed_client: Embedder,
    vector_index: VectorSearcher,
    keyword_index: KeywordSearcher,
    reranker: Reranker,
    chat_client: ChatClient,
) -> PipelineResult:
    retrieval_start = time.monotonic()
    context_chunks = retrieve(
        question, allowed_groups, embed_client, vector_index, keyword_index, reranker
    )
    retrieval_ms = (time.monotonic() - retrieval_start) * 1000

    generation_start = time.monotonic()
    answer = generate_answer(question, context_chunks, chat_client)
    generation_ms = (time.monotonic() - generation_start) * 1000

    return PipelineResult(
        answer=answer,
        retrieved_chunk_ids=[chunk.chunk_id for chunk in context_chunks],
        retrieval_ms=retrieval_ms,
        generation_ms=generation_ms,
    )
