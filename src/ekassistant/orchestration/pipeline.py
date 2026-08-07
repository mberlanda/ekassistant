"""Fixed request pipeline: identity -> retrieve -> generate -> answer.

See docs/design/orchestration.md. Approval gates and the tool layer stay
doc-level seams only (see ADR-0008's consequences) rather than literal
empty-registry code, since V1 has no write action or tool call to gate -
this function genuinely is the entire pipeline for now.
"""

from ekassistant.models.embedder import Embedder
from ekassistant.models.generation import ChatClient, generate_answer
from ekassistant.models.schema import GroundedAnswer
from ekassistant.retrieval.reranker import Reranker
from ekassistant.retrieval.retriever import KeywordSearcher, VectorSearcher, retrieve


def answer_question(
    question: str,
    allowed_groups: list[str],
    embed_client: Embedder,
    vector_index: VectorSearcher,
    keyword_index: KeywordSearcher,
    reranker: Reranker,
    chat_client: ChatClient,
) -> GroundedAnswer:
    context_chunks = retrieve(
        question, allowed_groups, embed_client, vector_index, keyword_index, reranker
    )
    return generate_answer(question, context_chunks, chat_client)
