import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from ekassistant.api.main import app, get_ollama_client, get_reranker, get_vector_index
from ekassistant.config.settings import Settings, get_settings
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.types import IndexedChunk, SearchResult
from ekassistant.retrieval.reranker import PassthroughReranker


@pytest.fixture(autouse=True)
def _isolated_trace_log(tmp_path, monkeypatch):
    # query() reads settings.trace_log_path directly (not via Depends()),
    # so without this every test in this file would append real trace
    # lines into the developer's actual data/traces.jsonl on disk -
    # confirmed happening before this fixture existed. get_settings() is
    # @lru_cache'd, so the env var must be set AND the cache cleared, both
    # before the test (to pick up the override) and after (so a later
    # test/session doesn't keep using this tmp_path after it's gone).
    monkeypatch.setenv("TRACE_LOG_PATH", str(tmp_path / "traces.jsonl"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeOllamaClient:
    """Doubles as both the embedder and the chat client, same as the real
    OllamaClient does - see docs/design/model-layer.md.
    """

    def __init__(self, chat_response: str):
        self._chat_response = chat_response
        self.temperatures_seen: list[float] = []

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
        self.temperatures_seen.append(temperature)
        return self._chat_response


class RaisingOllamaClient:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
        raise ConnectionError("ollama is not reachable")


class FakeVectorIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, query_embedding, allowed_groups, top_n):
        return self._results if allowed_groups else []


class FakeKeywordIndex:
    def search(self, query_text, allowed_groups, top_n):
        return []


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id, source="policy.md", text="The VPN needs MFA.", score=1.0
    )


def _real_keyword_index(tmp_path, filename="keyword_index.sqlite3") -> SqliteKeywordIndex:
    settings = Settings(keyword_index_path=tmp_path / filename)
    return SqliteKeywordIndex(settings)


def _override(monkeypatch, *, chat_response, vector_hits, keyword_index_getter):
    app.dependency_overrides[get_ollama_client] = lambda: FakeOllamaClient(chat_response)
    app.dependency_overrides[get_vector_index] = lambda: FakeVectorIndex(vector_hits)
    app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    monkeypatch.setattr("ekassistant.api.main.get_keyword_index", keyword_index_getter)


def teardown_function():
    app.dependency_overrides.clear()


def test_query_returns_grounded_answer_with_citations(tmp_path, monkeypatch):
    response_json = (
        '{"answer": "Yes, MFA is required.", '
        '"citations": [{"chunk_id": "c1", "source": "policy.md"}], "abstained": false, '
        '"confidence": 0.92}'
    )
    _override(
        monkeypatch,
        chat_response=response_json,
        vector_hits=[_hit("c1")],
        keyword_index_getter=lambda: _real_keyword_index(tmp_path),
    )

    resp = TestClient(app).post(
        "/query", json={"question": "does the vpn need mfa?"}, headers={"X-User-Id": "alice"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["abstained"] is False
    assert body["answer"] == "Yes, MFA is required."
    assert body["citations"] == [{"chunk_id": "c1", "source": "policy.md"}]
    assert body["confidence"] == 0.92


def test_query_abstain_reports_no_confidence(tmp_path, monkeypatch):
    _override(
        monkeypatch,
        chat_response="should never be read",
        vector_hits=[],
        keyword_index_getter=lambda: _real_keyword_index(tmp_path),
    )

    resp = TestClient(app).post(
        "/query", json={"question": "does the vpn need mfa?"}, headers={"X-User-Id": "alice"}
    )

    assert resp.json()["confidence"] is None


def test_query_uses_the_configured_default_temperature_when_not_specified(
    tmp_path, monkeypatch
):
    response_json = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.5}'
    )
    ollama_client = FakeOllamaClient(response_json)
    app.dependency_overrides[get_ollama_client] = lambda: ollama_client
    app.dependency_overrides[get_vector_index] = lambda: FakeVectorIndex([_hit("c1")])
    app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    monkeypatch.setattr(
        "ekassistant.api.main.get_keyword_index", lambda: _real_keyword_index(tmp_path)
    )

    TestClient(app).post(
        "/query", json={"question": "does the vpn need mfa?"}, headers={"X-User-Id": "alice"}
    )

    assert ollama_client.temperatures_seen == [get_settings().ollama_temperature]


def test_query_temperature_override_is_passed_through(tmp_path, monkeypatch):
    response_json = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.5}'
    )
    ollama_client = FakeOllamaClient(response_json)
    app.dependency_overrides[get_ollama_client] = lambda: ollama_client
    app.dependency_overrides[get_vector_index] = lambda: FakeVectorIndex([_hit("c1")])
    app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    monkeypatch.setattr(
        "ekassistant.api.main.get_keyword_index", lambda: _real_keyword_index(tmp_path)
    )

    resp = TestClient(app).post(
        "/query",
        json={"question": "does the vpn need mfa?", "temperature": 1.1},
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 200
    assert ollama_client.temperatures_seen == [1.1]


def test_query_rejects_an_out_of_range_temperature(tmp_path, monkeypatch):
    _override(
        monkeypatch,
        chat_response="should never be read",
        vector_hits=[],
        keyword_index_getter=lambda: _real_keyword_index(tmp_path),
    )

    resp = TestClient(app).post(
        "/query",
        json={"question": "does the vpn need mfa?", "temperature": 5.0},
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 422


def test_query_abstains_when_nothing_is_retrieved(tmp_path, monkeypatch):
    _override(
        monkeypatch,
        chat_response="should never be read",
        vector_hits=[],
        keyword_index_getter=lambda: _real_keyword_index(tmp_path),
    )

    resp = TestClient(app).post(
        "/query", json={"question": "does the vpn need mfa?"}, headers={"X-User-Id": "alice"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["abstained"] is True
    assert body["answer"] == ""
    assert body["citations"] == []


def test_query_for_unknown_user_fails_closed_to_abstain(tmp_path, monkeypatch):
    # An unrecognized user resolves to zero groups (ADR-0007), so
    # FakeVectorIndex.search returns [] regardless of what hits it was
    # constructed with - proving identity resolution and ACL fail-closed
    # behavior are actually wired together through /query end to end,
    # not just each tested in isolation.
    _override(
        monkeypatch,
        chat_response="should never be read",
        vector_hits=[_hit("c1")],
        keyword_index_getter=lambda: _real_keyword_index(tmp_path),
    )

    resp = TestClient(app).post(
        "/query",
        json={"question": "does the vpn need mfa?"},
        headers={"X-User-Id": "someone-not-in-identities-yaml"},
    )

    assert resp.status_code == 200
    assert resp.json()["abstained"] is True


def test_query_reaches_the_real_keyword_index_via_its_direct_call(tmp_path, monkeypatch):
    # get_keyword_index() is called directly inside query() rather than
    # injected via Depends() (see the comment on that call site for why).
    # Prove it's actually reached, not just an unused import: upsert a
    # real chunk into a real (throwaway file) SqliteKeywordIndex, force
    # the vector side to return nothing, and confirm /query's keyword
    # search path finds it.
    real_index = _real_keyword_index(tmp_path, filename="real_keyword_index.sqlite3")
    real_index.upsert(
        IndexedChunk(
            chunk_id="c1",
            source="policy.md",
            text="The VPN requires MFA.",
            allowed_groups=["engineering"],
        )
    )
    real_index.close()
    response_json = (
        '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
        '"abstained": false, "confidence": 0.5}'
    )
    _override(
        monkeypatch,
        chat_response=response_json,
        vector_hits=[],
        keyword_index_getter=lambda: _real_keyword_index(
            tmp_path, filename="real_keyword_index.sqlite3"
        ),
    )

    resp = TestClient(app).post(
        "/query", json={"question": "vpn mfa"}, headers={"X-User-Id": "alice"}
    )

    assert resp.status_code == 200
    assert resp.json()["citations"] == [{"chunk_id": "c1", "source": "policy.md"}]


def test_infrastructure_failure_surfaces_as_server_error_not_a_false_abstain(monkeypatch):
    # An Ollama outage must show up as a 5xx, never as a 200 "abstained"
    # response - the latter would misrepresent "the system is down" as
    # "nothing relevant was found", exactly what generation.py's and
    # retriever.py's docstrings say must never happen. No try/except
    # exists around answer_question() in query() today; this pins that.
    app.dependency_overrides[get_ollama_client] = lambda: RaisingOllamaClient()
    app.dependency_overrides[get_vector_index] = lambda: FakeVectorIndex([_hit("c1")])
    app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    monkeypatch.setattr("ekassistant.api.main.get_keyword_index", FakeKeywordIndex)

    resp = TestClient(app, raise_server_exceptions=False).post(
        "/query", json={"question": "does the vpn need mfa?"}, headers={"X-User-Id": "alice"}
    )

    assert resp.status_code == 500


def test_concurrent_requests_do_not_hit_sqlite_cross_thread_errors(tmp_path, monkeypatch):
    # Regression test for a real bug found in review: get_keyword_index()
    # used to be a FastAPI Depends() parameter. FastAPI resolves each sync
    # Depends() callable through its own separate threadpool dispatch, not
    # guaranteed to land on the same OS thread as the route handler body -
    # so a SqliteKeywordIndex built that way could be constructed on one
    # thread and searched on another, which sqlite3 forbids. Verified
    # concretely before the fix: with only 3 truly-concurrent requests,
    # all 3 failed with sqlite3.ProgrammingError. TestClient's normal
    # sequential .post() calls never exercise this, since they only ever
    # run one request at a time - this drives real concurrency (asyncio
    # .gather over the ASGI app) against the current, fixed code.
    response_json = '{"answer": "", "citations": [], "abstained": true, "confidence": 0.1}'
    _override(
        monkeypatch,
        chat_response=response_json,
        vector_hits=[],
        keyword_index_getter=lambda: _real_keyword_index(tmp_path),
    )

    async def run_concurrent_requests():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/query",
                        json={"question": f"question {i}"},
                        headers={"X-User-Id": "alice"},
                    )
                    for i in range(20)
                ]
            )

    responses = asyncio.run(run_concurrent_requests())

    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
