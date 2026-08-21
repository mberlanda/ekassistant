"""KnowledgeQaWorkflow: V1's answer_question() reached through the
Workflow/WorkflowStep shapes, plus the bind()/unbound-template split
this module documents at length.
"""

import pytest

from ekassistant.capabilities.contracts import Zone
from ekassistant.index.types import SearchResult
from ekassistant.retrieval.reranker import PassthroughReranker
from ekassistant.runs.aggregate import Run, RunStatus
from ekassistant.runs.service import StepExecutionFailed, execute_workflow
from ekassistant.runs.store import InMemoryRunStore
from ekassistant.workflows.base import WorkflowContext
from ekassistant.workflows.knowledge_qa import KnowledgeQaParams, KnowledgeQaWorkflow


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]


class FakeVectorIndex:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, query_embedding, allowed_groups, top_n):
        return self._results


class FakeKeywordIndex:
    def search(self, query_text, allowed_groups, top_n):
        return []


class FakeChatClient:
    def __init__(self, response: str):
        self._response = response

    def chat_json(self, system, user, json_schema, temperature):
        return self._response


def _hit(chunk_id: str) -> SearchResult:
    return SearchResult(chunk_id=chunk_id, source="policy.md", text="The VPN needs MFA.", score=1.0)


def _params(**overrides) -> KnowledgeQaParams:
    defaults = dict(
        question="does the vpn need mfa?",
        allowed_groups=["engineering"],
        embed_client=FakeEmbedder(),
        vector_index=FakeVectorIndex([_hit("c1")]),
        keyword_index=FakeKeywordIndex(),
        reranker=PassthroughReranker(),
        chat_client=FakeChatClient(
            '{"answer": "Yes.", "citations": [{"chunk_id": "c1", "source": "policy.md"}], '
            '"abstained": false, "confidence": 0.9}'
        ),
    )
    return KnowledgeQaParams(**{**defaults, **overrides})


# -- template metadata / bind() ----------------------------------------


def test_the_unbound_template_declares_static_metadata_only():
    template = KnowledgeQaWorkflow()
    assert template.task_type == "knowledge_qa"
    assert template.version == 1
    assert template.required_zones == frozenset({Zone.INTERNAL_DATA})
    assert template.required_scopes == frozenset({"kb.documents.read"})


def test_calling_steps_on_the_unbound_template_raises():
    with pytest.raises(RuntimeError):
        KnowledgeQaWorkflow().steps()


def test_bind_returns_an_executable_instance_carrying_the_same_metadata():
    bound = KnowledgeQaWorkflow().bind(_params())
    assert bound.task_type == "knowledge_qa"
    assert bound.required_scopes == frozenset({"kb.documents.read"})
    assert len(bound.steps()) == 1


def test_steps_returns_the_same_step_instance_every_call():
    # execute_workflow() and the API route both need to observe .result
    # on the *executed* step - a fresh KnowledgeQaStep per steps() call
    # would silently hand back an object that was never run.
    bound = KnowledgeQaWorkflow().bind(_params())
    assert bound.steps()[0] is bound.steps()[0]


# -- step execution -------------------------------------------------


def make_run(**kwargs) -> Run:
    defaults = dict(
        tenant_id="acme",
        principal_id="alice",
        purpose="knowledge_qa",
        task_type="knowledge_qa",
        approved_scopes=frozenset({"kb.documents.read"}),
        allowed_zones=frozenset({Zone.INTERNAL_DATA}),
    )
    return Run(**{**defaults, **kwargs})


def test_a_grounded_answer_completes_the_run_and_records_evidence():
    store = InMemoryRunStore()
    run = store.create(make_run())
    workflow = KnowledgeQaWorkflow().bind(_params())

    final = execute_workflow(run, workflow, store, gateway=None, ledger=None)

    assert final.status.value == "COMPLETED"
    assert final.evidence_refs == ("c1",)
    step = workflow.steps()[0]
    assert step.result is not None
    assert step.result.answer.answer == "Yes."
    assert step.result.answer.citations[0].chunk_id == "c1"


def test_an_abstain_still_completes_the_run_with_no_citations():
    store = InMemoryRunStore()
    run = store.create(make_run())
    workflow = KnowledgeQaWorkflow().bind(
        _params(vector_index=FakeVectorIndex([]), keyword_index=FakeKeywordIndex())
    )

    final = execute_workflow(run, workflow, store, gateway=None, ledger=None)

    assert final.status.value == "COMPLETED"
    assert final.evidence_refs == ()
    step = workflow.steps()[0]
    assert step.result.answer.abstained is True
    assert [s.detail for s in final.step_results] == ["abstained: empty_context"]


def test_infrastructure_failure_propagates_rather_than_a_false_abstain():
    # Mirrors test_api_query.py's pin on /query: an Ollama-shaped
    # transport error must never be swallowed into a false "abstained"
    # StepOutcome. answer_question() already does not catch it (see
    # orchestration/pipeline.py); this proves the workflow step does not
    # add a catch of its own.
    class RaisingChatClient:
        def chat_json(self, system, user, json_schema, temperature):
            raise ConnectionError("ollama is not reachable")

    workflow = KnowledgeQaWorkflow().bind(_params(chat_client=RaisingChatClient()))
    step = workflow.steps()[0]

    # The step itself: raises, rather than returning ok=True with an
    # abstain. This is the property that must never regress.
    with pytest.raises(ConnectionError):
        step.execute(WorkflowContext(run=make_run(), gateway=None, ledger=None))


def test_the_driver_halts_the_run_rather_than_letting_the_error_escape_untracked():
    # execute_workflow wraps the same failure in StepExecutionFailed so
    # the Run is persisted at FAILED_RETRYABLE instead of stranded at
    # RUNNING - see tests/test_run_service.py. The original error is
    # chained, not swallowed: the "no false abstain" property above still
    # holds one layer up.
    class RaisingChatClient:
        def chat_json(self, system, user, json_schema, temperature):
            raise ConnectionError("ollama is not reachable")

    store = InMemoryRunStore()
    run = store.create(make_run())
    workflow = KnowledgeQaWorkflow().bind(_params(chat_client=RaisingChatClient()))

    with pytest.raises(StepExecutionFailed) as caught:
        execute_workflow(run, workflow, store, gateway=None, ledger=None)

    assert isinstance(caught.value.__cause__, ConnectionError)
    assert store.get(run.run_id).status is RunStatus.FAILED_RETRYABLE


# -- capability-scope deny path (new with this workflow) ----------------


def test_a_run_without_kb_documents_read_is_rejected_before_retrieval_runs():
    # config/policies.yaml grants kb.documents.read to all-staff and
    # engineering; guest (config/identities.yaml) holds neither, so a
    # guest Run declaring knowledge_qa never reaches the index adapters
    # at all under this workflow - a stricter, capability-scope-shaped
    # deny path that sits alongside (not instead of) V1's per-chunk ACL
    # enforcement, which guest also fails.
    store = InMemoryRunStore()
    guest_run = make_run(
        principal_id="guest",
        approved_scopes=frozenset(),
        allowed_zones=frozenset({Zone.INTERNAL_DATA}),
    )
    run = store.create(guest_run)
    calls = []

    class SpyVectorIndex:
        def search(self, query_embedding, allowed_groups, top_n):
            calls.append(allowed_groups)
            return []

    workflow = KnowledgeQaWorkflow().bind(
        _params(allowed_groups=[], vector_index=SpyVectorIndex(), keyword_index=FakeKeywordIndex())
    )

    final = execute_workflow(run, workflow, store, gateway=None, ledger=None)

    assert final.status.value == "REJECTED_POLICY"
    assert "kb.documents.read" in final.terminal_reason
    assert calls == []
