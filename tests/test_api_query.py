from fastapi.testclient import TestClient

from ekassistant.api.main import (
    app,
    get_keyword_index,
    get_ollama_client,
    get_reranker,
    get_vector_index,
)
from ekassistant.config.settings import Settings
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.types import IndexedChunk, SearchResult
from ekassistant.retrieval.reranker import PassthroughReranker


class FakeOllamaClient:
    """Doubles as both the embedder and the chat client, same as the real
    OllamaClient does - see docs/design/model-layer.md.
    """

    def __init__(self, chat_response: str):
        self._chat_response = chat_response

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    def chat_json(self, system: str, user: str, json_schema: dict) -> str:
        return self._chat_response


class FakeVectorIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, query_embedding, allowed_groups, top_n):
        return self._results if allowed_groups else []


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id, source="policy.md", text="The VPN needs MFA.", score=1.0
    )


def _keyword_index_factory(tmp_path, filename="keyword_index.sqlite3"):
    """Returns a callable matching get_keyword_index's own shape: called
    fresh per request, opening a new sqlite3 connection each time rather
    than sharing one across threads. TestClient dispatches sync route
    handlers onto a worker thread, so a pre-built SqliteKeywordIndex
    handed straight to dependency_overrides would hit the exact
    cross-thread sqlite3.ProgrammingError get_keyword_index's own
    per-request-construction is designed to avoid - reproduced this
    directly while writing this test, which is why the factory here
    mirrors the real dependency's shape instead of capturing one instance.
    """
    settings = Settings(keyword_index_path=tmp_path / filename)

    def factory() -> SqliteKeywordIndex:
        return SqliteKeywordIndex(settings)

    return factory


def _override_dependencies(*, chat_response, vector_hits, keyword_index_factory):
    app.dependency_overrides[get_ollama_client] = lambda: FakeOllamaClient(chat_response)
    app.dependency_overrides[get_vector_index] = lambda: FakeVectorIndex(vector_hits)
    app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    app.dependency_overrides[get_keyword_index] = keyword_index_factory


def teardown_function():
    app.dependency_overrides.clear()


def test_query_returns_grounded_answer_with_citations(tmp_path):
    response_json = (
        '{"answer": "Yes, MFA is required.", '
        '"citations": [{"chunk_id": "c1", "source": "policy.md"}], "abstained": false}'
    )
    _override_dependencies(
        chat_response=response_json,
        vector_hits=[_hit("c1")],
        keyword_index_factory=_keyword_index_factory(tmp_path),
    )

    resp = TestClient(app).post(
        "/query", json={"question": "does the vpn need mfa?"}, headers={"X-User-Id": "alice"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["abstained"] is False
    assert body["answer"] == "Yes, MFA is required."
    assert body["citations"] == [{"chunk_id": "c1", "source": "policy.md"}]


def test_query_abstains_when_nothing_is_retrieved(tmp_path):
    _override_dependencies(
        chat_response="should never be read",
        vector_hits=[],
        keyword_index_factory=_keyword_index_factory(tmp_path),
    )

    resp = TestClient(app).post(
        "/query", json={"question": "does the vpn need mfa?"}, headers={"X-User-Id": "alice"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["abstained"] is True
    assert body["answer"] == ""
    assert body["citations"] == []


def test_query_for_unknown_user_fails_closed_to_abstain(tmp_path):
    # An unrecognized user resolves to zero groups (ADR-0007), so
    # FakeVectorIndex.search returns [] regardless of what hits it was
    # constructed with - proving identity resolution and ACL fail-closed
    # behavior are actually wired together through /query end to end,
    # not just each tested in isolation.
    _override_dependencies(
        chat_response="should never be read",
        vector_hits=[_hit("c1")],
        keyword_index_factory=_keyword_index_factory(tmp_path),
    )

    resp = TestClient(app).post(
        "/query",
        json={"question": "does the vpn need mfa?"},
        headers={"X-User-Id": "someone-not-in-identities-yaml"},
    )

    assert resp.status_code == 200
    assert resp.json()["abstained"] is True


def test_query_uses_the_real_keyword_index_dependency_when_not_overridden(tmp_path):
    # Prove get_keyword_index is actually wired through Depends() and not
    # just an unused parameter: upsert a real chunk into a real (throwaway
    # file) SqliteKeywordIndex, force the vector side to return nothing,
    # and confirm /query's keyword search path finds it - via a fresh
    # connection to the same file, matching how the real dependency
    # behaves per request.
    factory = _keyword_index_factory(tmp_path, filename="real_keyword_index.sqlite3")
    setup_connection = factory()
    setup_connection.upsert(
        IndexedChunk(
            chunk_id="c1",
            source="policy.md",
            text="The VPN requires MFA.",
            allowed_groups=["engineering"],
        )
    )
    setup_connection.close()

    response_json = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false}'
    )
    _override_dependencies(
        chat_response=response_json, vector_hits=[], keyword_index_factory=factory
    )

    resp = TestClient(app).post(
        "/query", json={"question": "vpn mfa"}, headers={"X-User-Id": "alice"}
    )

    assert resp.status_code == 200
    assert resp.json()["citations"] == [{"chunk_id": "c1", "source": "policy.md"}]
