"""API Gateway & Identity.

See docs/design/api-gateway-identity.md and docs/design/orchestration.md.
Mock-authenticates a caller, resolves their groups, and runs the full
identity -> retrieve -> generate pipeline for POST /query.
"""

from functools import lru_cache
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel

from ekassistant.config.settings import get_settings
from ekassistant.identity.store import IdentityStore
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.vector_index import QdrantVectorIndex
from ekassistant.models.ollama_client import OllamaClient
from ekassistant.orchestration.pipeline import answer_question
from ekassistant.retrieval.reranker import PassthroughReranker

app = FastAPI(title="Enterprise Knowledge Assistant - API Gateway")


@lru_cache
def get_identity_store() -> IdentityStore:
    return IdentityStore.from_yaml(get_settings().identities_path)


@lru_cache
def get_ollama_client() -> OllamaClient:
    return OllamaClient(get_settings())


@lru_cache
def get_vector_index() -> QdrantVectorIndex:
    index = QdrantVectorIndex(get_settings())
    index.ensure_collection()
    return index


def get_keyword_index() -> SqliteKeywordIndex:
    # Deliberately NOT registered as a FastAPI Depends() parameter below,
    # unlike the other three dependencies - see the call site in query()
    # for why. Kept as a plain module-level function (not @lru_cache'd)
    # so it can still be monkeypatched directly in tests.
    return SqliteKeywordIndex(get_settings())


@lru_cache
def get_reranker() -> PassthroughReranker:
    return PassthroughReranker()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/whoami")
def whoami(x_user_id: str | None = Header(default=None)) -> dict:
    """Resolve the mock caller identity to their group set.

    See docs/decisions/0007-mock-identity-and-group-lookup.md: an absent
    header falls back to the configured default user, and an unrecognized
    user resolves to an empty group set rather than an error.
    """
    settings = get_settings()
    user_id = x_user_id or settings.default_user
    groups = get_identity_store().groups_for(user_id)
    return {"user_id": user_id, "groups": groups}


class QueryRequest(BaseModel):
    question: str


class CitationResponse(BaseModel):
    chunk_id: str
    source: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    abstained: bool


@app.post("/query")
def query(
    request: QueryRequest,
    ollama_client: Annotated[OllamaClient, Depends(get_ollama_client)],
    vector_index: Annotated[QdrantVectorIndex, Depends(get_vector_index)],
    reranker: Annotated[PassthroughReranker, Depends(get_reranker)],
    x_user_id: str | None = Header(default=None),
) -> QueryResponse:
    """Grounded, cite-or-abstain question answering.

    See docs/design/orchestration.md. Identity resolution is the same
    mock lookup /whoami uses; ACL enforcement itself happens inside the
    index adapters during retrieval (ADR-0002), not here.

    keyword_index is deliberately called directly here, NOT injected via
    Depends() like the other three dependencies. FastAPI resolves each
    sync Depends() callable through its own separate threadpool dispatch,
    which is NOT guaranteed to land on the same OS thread as the route
    handler body - so a SqliteKeywordIndex built via Depends() could be
    constructed on one thread and then have .search() called on it (deep
    inside answer_question() -> retrieve()) on another, which sqlite3
    forbids. Verified this concretely: under real concurrent requests
    (asyncio.gather against the ASGI app, not just sequential calls),
    building it via Depends() failed the large majority of requests with
    sqlite3.ProgrammingError. Constructing it inside this function body
    keeps construction and use in the same synchronous call stack, and
    therefore the same thread, regardless of concurrency.
    """
    settings = get_settings()
    user_id = x_user_id or settings.default_user
    groups = get_identity_store().groups_for(user_id)
    keyword_index = get_keyword_index()

    # ollama_client is passed twice on purpose, not a copy-paste slip:
    # OllamaClient implements both the Embedder and ChatClient protocols
    # (see docs/design/model-layer.md), and answer_question() needs one
    # of each. Keyword arguments here, not positional, since several of
    # these parameters are structurally similar enough (two OllamaClient
    # instances, two Protocol-shaped index searchers) that a positional
    # transposition would run without a type error and just silently
    # misbehave.
    result = answer_question(
        question=request.question,
        allowed_groups=groups,
        embed_client=ollama_client,
        vector_index=vector_index,
        keyword_index=keyword_index,
        reranker=reranker,
        chat_client=ollama_client,
    )
    return QueryResponse(
        answer=result.answer,
        citations=[
            CitationResponse(chunk_id=c.chunk_id, source=c.source) for c in result.citations
        ],
        abstained=result.abstained,
    )


def run() -> None:
    uvicorn.run("ekassistant.api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
