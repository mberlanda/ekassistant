"""POST /runs and GET /runs/{id}.

Deny paths first, per this repo's convention: an unknown purpose, a
task_type nobody registered a workflow for, a missing required input, a
Run that never held the right capability scope, budget exhaustion, and -
the one that must never regress - a caller reading another principal's
Run.
"""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from ekassistant.api.main import (
    app,
    get_capability_registry,
    get_effect_ledger,
    get_ollama_client,
    get_policy_engine,
    get_reranker,
    get_run_store,
    get_vector_index,
    get_workflow_registry,
)
from ekassistant.config.settings import Settings, get_settings
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.types import SearchResult
from ekassistant.retrieval.reranker import PassthroughReranker


@pytest.fixture(autouse=True)
def _isolated_run_service(tmp_path, monkeypatch):
    # Every store/registry this route touches is @lru_cache'd on the
    # module - each needs its cache cleared alongside get_settings' own,
    # or a test would silently reuse another test's (or the developer's
    # real) database, registry or audit log. Same pattern as
    # test_api_query.py's _isolated_trace_log, extended to the new
    # caches this phase adds.
    monkeypatch.setenv("TRACE_LOG_PATH", str(tmp_path / "traces.jsonl"))
    monkeypatch.setenv("RUNS_DB_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    caches = [
        get_settings,
        get_run_store,
        get_capability_registry,
        get_policy_engine,
        get_effect_ledger,
        get_workflow_registry,
    ]
    for cache in caches:
        cache.cache_clear()
    yield
    for cache in caches:
        cache.cache_clear()


class FakeOllamaClient:
    def __init__(self, chat_response: str):
        self._chat_response = chat_response

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
        return self._chat_response


class FakeVectorIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, query_embedding, allowed_groups, top_n):
        return self._results if allowed_groups else []


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, source="policy.md", text="The VPN needs MFA.", score=1.0)


def _real_keyword_index(tmp_path, filename="keyword_index.sqlite3") -> SqliteKeywordIndex:
    return SqliteKeywordIndex(Settings(keyword_index_path=tmp_path / filename))


def _override(monkeypatch, tmp_path, *, chat_response, vector_hits):
    app.dependency_overrides[get_ollama_client] = lambda: FakeOllamaClient(chat_response)
    app.dependency_overrides[get_vector_index] = lambda: FakeVectorIndex(vector_hits)
    app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    monkeypatch.setattr(
        "ekassistant.api.main.get_keyword_index", lambda: _real_keyword_index(tmp_path)
    )


def teardown_function():
    app.dependency_overrides.clear()


ANSWER_JSON = (
    '{"answer": "Yes, MFA is required.", '
    '"citations": [{"chunk_id": "c1", "source": "policy.md"}], "abstained": false, '
    '"confidence": 0.92}'
)


# -- deny paths -----------------------------------------------------


def test_unknown_purpose_is_rejected_with_403(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).post(
        "/runs",
        json={"purpose": "do_anything", "task_type": "knowledge_qa", "input": {"question": "hi"}},
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 403
    assert "unknown purpose" in resp.json()["detail"]


def test_task_type_not_permitted_by_purpose_is_rejected_with_403(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).post(
        "/runs",
        json={
            "purpose": "market_research",
            "task_type": "knowledge_qa",
            "input": {"question": "hi"},
        },
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 403
    assert "not permitted for purpose" in resp.json()["detail"]


def test_unregistered_task_type_is_rejected_with_400(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).post(
        "/runs",
        json={
            "purpose": "client_communication",
            "task_type": "email_draft",
            "input": {},
        },
        headers={"X-User-Id": "bob"},
    )

    assert resp.status_code == 400
    assert "no workflow registered" in resp.json()["detail"]


def test_missing_question_input_is_rejected_with_422(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).post(
        "/runs",
        json={"purpose": "knowledge_qa", "task_type": "knowledge_qa", "input": {}},
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 422
    assert "input.question" in resp.json()["detail"]
    # A 422 here must not leave an abandoned RECEIVED Run behind - the
    # input-validation check runs before run_store.create(), not after.
    assert get_run_store().list_for_principal("acme", "alice") == []


def test_unregistered_task_type_leaves_no_orphaned_run(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    TestClient(app).post(
        "/runs",
        json={"purpose": "client_communication", "task_type": "email_draft", "input": {}},
        headers={"X-User-Id": "bob"},
    )

    assert get_run_store().list_for_principal("acme", "bob") == []


def test_guest_lacks_kb_documents_read_so_the_run_is_rejected_before_retrieval(
    tmp_path, monkeypatch
):
    # guest holds no groups (config/identities.yaml) and therefore no
    # scopes. The HTTP call itself still succeeds (a Run record was
    # created) - the refusal shows up in the Run's own terminal status,
    # not as an HTTP error, since intake accepted a valid purpose/
    # task_type combination and only the workflow-level sufficiency
    # check is what actually refuses.
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).post(
        "/runs",
        json={
            "purpose": "knowledge_qa",
            "task_type": "knowledge_qa",
            "input": {"question": "does the vpn need mfa?"},
        },
        headers={"X-User-Id": "guest"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["run"]["status"] == "REJECTED_POLICY"
    assert "kb.documents.read" in body["run"]["terminal_reason"]
    assert body["result"] is None


def test_budget_exhausted_before_the_step_runs_ends_the_run_without_a_result(
    tmp_path, monkeypatch
):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).post(
        "/runs",
        json={
            "purpose": "knowledge_qa",
            "task_type": "knowledge_qa",
            "input": {"question": "does the vpn need mfa?"},
            "budget": {"max_steps": 0},
        },
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["run"]["status"] == "BUDGET_EXHAUSTED"
    assert "max_steps" in body["run"]["terminal_reason"]
    assert body["result"] is None


def test_a_past_deadline_ends_the_run_as_timed_out(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).post(
        "/runs",
        json={
            "purpose": "knowledge_qa",
            "task_type": "knowledge_qa",
            "input": {"question": "does the vpn need mfa?"},
            "budget": {"deadline_seconds": -1},
        },
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 201
    assert resp.json()["run"]["status"] == "TIMED_OUT"


def test_get_unknown_run_is_404(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    resp = TestClient(app).get("/runs/run_does_not_exist", headers={"X-User-Id": "alice"})
    assert resp.status_code == 404


def test_get_another_principals_run_is_404_not_403(tmp_path, monkeypatch):
    # The core deny path for this endpoint: a 403 would confirm the
    # run_id exists and just is not this caller's, leaking information a
    # caller with no legitimate access should not get. It must be
    # indistinguishable from "never existed".
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])
    client = TestClient(app)

    created = client.post(
        "/runs",
        json={
            "purpose": "knowledge_qa",
            "task_type": "knowledge_qa",
            "input": {"question": "does the vpn need mfa?"},
        },
        headers={"X-User-Id": "alice"},
    )
    run_id = created.json()["run"]["run_id"]

    as_bob = client.get(f"/runs/{run_id}", headers={"X-User-Id": "bob"})
    assert as_bob.status_code == 404

    as_owner = client.get(f"/runs/{run_id}", headers={"X-User-Id": "alice"})
    assert as_owner.status_code == 200


# -- allow path -------------------------------------------------------


def test_a_grounded_answer_completes_the_run_and_is_returned_once(tmp_path, monkeypatch):
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])
    client = TestClient(app)

    resp = client.post(
        "/runs",
        json={
            "purpose": "knowledge_qa",
            "task_type": "knowledge_qa",
            "input": {"question": "does the vpn need mfa?"},
        },
        headers={"X-User-Id": "alice"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["run"]["status"] == "COMPLETED"
    assert body["run"]["evidence_refs"] == ["c1"]
    assert body["result"]["answer"] == "Yes, MFA is required."
    assert body["result"]["citations"] == [{"chunk_id": "c1", "source": "policy.md"}]

    # GET never carries the answer - only the durable Run record.
    fetched = client.get(f"/runs/{body['run']['run_id']}", headers={"X-User-Id": "alice"})
    assert fetched.status_code == 200
    assert "result" not in fetched.json()
    assert fetched.json()["status"] == "COMPLETED"


def test_concurrent_run_creation_does_not_hit_cross_thread_sqlite_errors(tmp_path, monkeypatch):
    # Same shape as test_api_query.py's regression test for the keyword
    # index, extended to prove the new SqliteRunStore singleton
    # (get_run_store is @lru_cache'd, unlike get_keyword_index) is also
    # safe when many requests hit it from genuinely concurrent threads,
    # not a sequential loop.
    _override(monkeypatch, tmp_path, chat_response=ANSWER_JSON, vector_hits=[_hit("c1")])

    async def run_concurrent_requests():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/runs",
                        json={
                            "purpose": "knowledge_qa",
                            "task_type": "knowledge_qa",
                            "input": {"question": f"question {i}"},
                        },
                        headers={"X-User-Id": "alice"},
                    )
                    for i in range(20)
                ]
            )

    responses = asyncio.run(run_concurrent_requests())

    assert all(r.status_code == 201 for r in responses), [r.status_code for r in responses]
    run_ids = {r.json()["run"]["run_id"] for r in responses}
    assert len(run_ids) == 20


# -- an infrastructure failure mid-run ----------------------------------

_BODY = {
    "purpose": "knowledge_qa",
    "task_type": "knowledge_qa",
    "input": {"question": "does the VPN need MFA?"},
}



class ExplodingOllamaClient:
    """Stands in for Ollama being down mid-request. Neither
    OllamaClient nor orchestration/pipeline.py catches transport errors,
    so this is what a real outage looks like to the route.
    """

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    def chat_json(self, system, user, json_schema, temperature):
        raise ConnectionError("ollama unreachable")


def _explode(monkeypatch, tmp_path):
    app.dependency_overrides[get_ollama_client] = lambda: ExplodingOllamaClient()
    app.dependency_overrides[get_vector_index] = lambda: FakeVectorIndex([_hit("c1")])
    app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    monkeypatch.setattr(
        "ekassistant.api.main.get_keyword_index", lambda: _real_keyword_index(tmp_path)
    )


def test_a_provider_outage_does_not_leave_the_run_stuck_at_running(tmp_path, monkeypatch):
    # Regression: the Run used to stay durably at RUNNING with no reason,
    # indistinguishable from one still in progress that would never finish.
    _explode(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    client.post("/runs", json=_BODY, headers={"X-User-Id": "alice"})

    stored = get_run_store().list_for_principal("acme", "alice")
    assert [r.status.value for r in stored] == ["FAILED_RETRYABLE"]
    assert stored[0].terminal_reason is not None


def test_a_provider_outage_returns_the_run_id_so_the_record_is_reachable(tmp_path, monkeypatch):
    # A bare 500 makes the durable record the driver just wrote unreachable
    # by the only caller who wants it.
    _explode(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/runs", json=_BODY, headers={"X-User-Id": "alice"})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["status"] == "FAILED_RETRYABLE"

    followed_up = client.get(f"/runs/{detail['run_id']}", headers={"X-User-Id": "alice"})
    assert followed_up.status_code == 200
    assert followed_up.json()["status"] == "FAILED_RETRYABLE"


def test_a_provider_outage_still_charges_the_attempted_step(tmp_path, monkeypatch):
    _explode(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    client.post("/runs", json=_BODY, headers={"X-User-Id": "alice"})

    assert get_run_store().list_for_principal("acme", "alice")[0].spend.steps == 1


def test_a_provider_outage_leaks_no_answer_content_or_error_message(tmp_path, monkeypatch):
    _explode(monkeypatch, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/runs", json=_BODY, headers={"X-User-Id": "alice"})
    run_id = response.json()["detail"]["run_id"]
    body = client.get(f"/runs/{run_id}", headers={"X-User-Id": "alice"}).text

    assert "ollama unreachable" not in body
    assert "ConnectionError" in body  # the type is useful; the message is not
