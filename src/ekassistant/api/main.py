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
    # Deliberately NOT @lru_cache'd, unlike the other dependencies above:
    # sqlite3 connections are only usable from the thread that created
    # them (see docs/decisions/0005's adapter, and the constraint flagged
    # when SqliteKeywordIndex was introduced), and FastAPI can run a sync
    # route handler on a different threadpool thread per request. A fresh
    # connection per request sidesteps that entirely - SQLite connection
    # setup is cheap enough that this isn't a meaningful cost here.
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
    keyword_index: Annotated[SqliteKeywordIndex, Depends(get_keyword_index)],
    reranker: Annotated[PassthroughReranker, Depends(get_reranker)],
    x_user_id: str | None = Header(default=None),
) -> QueryResponse:
    """Grounded, cite-or-abstain question answering.

    See docs/design/orchestration.md. Identity resolution is the same
    mock lookup /whoami uses; ACL enforcement itself happens inside the
    index adapters during retrieval (ADR-0002), not here. Dependencies
    are FastAPI-injected (Depends) rather than called directly, so tests
    can override them with fakes via app.dependency_overrides without
    needing a live Ollama/Qdrant.
    """
    settings = get_settings()
    user_id = x_user_id or settings.default_user
    groups = get_identity_store().groups_for(user_id)

    result = answer_question(
        request.question,
        groups,
        ollama_client,
        vector_index,
        keyword_index,
        reranker,
        ollama_client,
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
