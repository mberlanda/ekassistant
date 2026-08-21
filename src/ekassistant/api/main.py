"""API Gateway & Identity.

See docs/design/api-gateway-identity.md and docs/design/orchestration.md.
Mock-authenticates a caller, resolves their groups, and runs the full
identity -> retrieve -> generate pipeline for POST /query.

POST /runs and GET /runs/{id} (see docs/design/runs.md) are the Phase 1
Run service, added alongside /query rather than in place of it - V1's
pipeline is untouched, and knowledge_qa is one workflow template a Run can
declare, not a replacement endpoint.
"""

import hashlib
import json
import time
from functools import lru_cache
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ekassistant.capabilities.audit import JsonlAuditSink
from ekassistant.capabilities.bootstrap import registry_from_yaml
from ekassistant.capabilities.effects import InMemoryEffectLedger
from ekassistant.capabilities.gateway import CapabilityGateway
from ekassistant.capabilities.registry import CapabilityRegistry
from ekassistant.config.settings import get_settings
from ekassistant.identity.store import IdentityStore
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.vector_index import QdrantVectorIndex
from ekassistant.models.ollama_client import OllamaClient
from ekassistant.observability.tracing import RunTrace, record_run
from ekassistant.orchestration.pipeline import answer_question
from ekassistant.policy.engine import ScopePolicyEngine
from ekassistant.retrieval.reranker import PassthroughReranker
from ekassistant.runs.aggregate import Budget, Run, RunStatus
from ekassistant.runs.intake import IntakeRejected, IntakeRequest, create_run
from ekassistant.runs.service import StepExecutionFailed, execute_workflow
from ekassistant.runs.sqlite_store import SqliteRunStore
from ekassistant.runs.store import RunNotFound
from ekassistant.workflows.knowledge_qa import KnowledgeQaParams, KnowledgeQaWorkflow
from ekassistant.workflows.registry import WorkflowNotFound, WorkflowRegistry

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


@lru_cache
def get_run_store() -> SqliteRunStore:
    # Safe to cache, unlike get_keyword_index(): SqliteRunStore never holds
    # a connection across calls, so there is no cross-thread connection to
    # worry about - see runs/sqlite_store.py's module docstring.
    return SqliteRunStore(get_settings().runs_db_path)


@lru_cache
def get_capability_registry() -> CapabilityRegistry:
    return registry_from_yaml(get_settings().capabilities_path)


@lru_cache
def get_policy_engine() -> ScopePolicyEngine:
    return ScopePolicyEngine.from_yaml(get_settings().policies_path)


@lru_cache
def get_effect_ledger() -> InMemoryEffectLedger:
    # Phase 3 owes a durable ledger (see docs/design/capabilities.md);
    # knowledge_qa never proposes an effect, so nothing in Phase 1
    # exercises this beyond the gateway's constructor requiring one.
    return InMemoryEffectLedger()


@lru_cache
def get_workflow_registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(KnowledgeQaWorkflow())
    return registry


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
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description=(
            "Generation temperature override for this request. Omit to use "
            "Settings.ollama_temperature (see the TUI's `:temp` command)."
        ),
    )


class CitationResponse(BaseModel):
    chunk_id: str
    source: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    abstained: bool
    confidence: float | None
    """The model's raw, unvalidated self-reported confidence - see
    GroundedAnswer.confidence. None on any abstain."""


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
    temperature = (
        request.temperature if request.temperature is not None else settings.ollama_temperature
    )

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
        temperature=temperature,
    )

    record_run(
        RunTrace(
            user_id=user_id,
            groups=groups,
            question=request.question,
            retrieved_chunk_ids=result.retrieved_chunk_ids,
            retrieval_ms=result.retrieval_ms,
            generation_ms=result.generation_ms,
            temperature=temperature,
            abstained=result.answer.abstained,
            abstain_reason=result.answer.reason,
            citation_count=len(result.answer.citations),
            confidence=result.answer.confidence,
            chat_model=settings.ollama_model,
            embed_model=settings.ollama_embed_model,
        ),
        settings.trace_log_path,
    )

    return QueryResponse(
        answer=result.answer.answer,
        citations=[
            CitationResponse(chunk_id=c.chunk_id, source=c.source)
            for c in result.answer.citations
        ],
        abstained=result.answer.abstained,
        confidence=result.answer.confidence,
    )


class RunBudgetOverride(BaseModel):
    """Caller-supplied budget overrides for one Run.

    Not policy-enforced - any caller can set their own limits. A real
    deployment would want a purpose- or tenant-driven ceiling on this
    (matching how policy already bounds zones and scopes) rather than
    trusting the request body outright; that is an accepted Phase 1 gap,
    called out in docs/design/runs.md, not an oversight. It exists at all
    so budget exhaustion - one of this phase's required deny paths - is
    reachable through the real HTTP endpoint in a test, not only through
    runs/service.py's unit tests.
    """

    max_steps: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)
    max_spend_micros: int | None = Field(default=None, ge=0)
    deadline_seconds: float | None = Field(
        default=None,
        description="Seconds from now until this Run's deadline. A value "
        "<= 0 creates an already-passed deadline, useful for exercising "
        "the TIMED_OUT path deterministically.",
    )


def _budget_from_override(override: RunBudgetOverride | None) -> Budget:
    defaults = Budget()
    if override is None:
        return defaults
    deadline_ts = (
        None if override.deadline_seconds is None else time.time() + override.deadline_seconds
    )
    return Budget(
        max_steps=override.max_steps if override.max_steps is not None else defaults.max_steps,
        max_tokens=(
            override.max_tokens if override.max_tokens is not None else defaults.max_tokens
        ),
        max_spend_micros=(
            override.max_spend_micros
            if override.max_spend_micros is not None
            else defaults.max_spend_micros
        ),
        deadline_ts=deadline_ts,
    )


class CreateRunRequest(BaseModel):
    purpose: str
    task_type: str
    # Deliberately a free-form dict rather than a knowledge_qa-shaped
    # model: this endpoint dispatches on task_type (see create_run_route),
    # and each workflow needs different input - a `question` string for
    # knowledge_qa today, something else entirely for the workflows Phases
    # 3-5 add. Validated per task_type in the route, not here.
    input: dict[str, Any] = Field(default_factory=dict)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    budget: RunBudgetOverride | None = None


class BudgetResponse(BaseModel):
    max_steps: int
    max_tokens: int
    max_spend_micros: int
    deadline_ts: float | None


class SpendResponse(BaseModel):
    steps: int
    tokens: int
    spend_micros: int


class RunResponse(BaseModel):
    """The durable Run record, and nothing else.

    No answer text, no draft body, no retrieved chunk content - see
    docs/design/runs.md#what-a-run-does-not-store. `evidence_refs` here
    are the retrieved chunk_ids for a knowledge_qa Run; the content they
    point at is not re-derivable from this response, only from the one
    POST /runs response that produced it (see CreateRunResponse.result).
    """

    run_id: str
    tenant_id: str
    principal_id: str
    purpose: str
    task_type: str
    status: str
    terminal_reason: str | None
    created_at: float
    updated_at: float
    evidence_refs: list[str]
    budget: BudgetResponse
    spend: SpendResponse


def _to_run_response(run: Run) -> RunResponse:
    return RunResponse(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        principal_id=run.principal_id,
        purpose=run.purpose,
        task_type=run.task_type,
        status=run.status.value,
        terminal_reason=run.terminal_reason,
        created_at=run.created_at,
        updated_at=run.updated_at,
        evidence_refs=list(run.evidence_refs),
        budget=BudgetResponse(
            max_steps=run.budget.max_steps,
            max_tokens=run.budget.max_tokens,
            max_spend_micros=run.budget.max_spend_micros,
            deadline_ts=run.budget.deadline_ts,
        ),
        spend=SpendResponse(
            steps=run.spend.steps, tokens=run.spend.tokens, spend_micros=run.spend.spend_micros
        ),
    )


class KnowledgeQaResultResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    abstained: bool
    confidence: float | None


class CreateRunResponse(BaseModel):
    run: RunResponse
    # Present only when a workflow that produces an inline, synchronous
    # result completed within this call - knowledge_qa always does. Never
    # persisted on the Run itself (see RunResponse's docstring), so a
    # later GET /runs/{id} for the same run_id will not have it: this is
    # a one-time echo to the caller who asked, the same trust boundary
    # V1's /query already draws around the answer text.
    result: KnowledgeQaResultResponse | None = None


def _input_ref(payload: dict[str, Any]) -> str:
    """A stable, content-derived reference for a Run's `input_refs`,
    never the input payload itself - see docs/design/runs.md#the-record.
    There is no separate input store in Phase 1, so this is a hash
    pointing at content that (today) only exists in the request that
    produced it, not a real dereferenceable id - an accepted
    simplification, not a claim that this id resolves to anything yet.
    """
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return "inline:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


@app.post("/runs", status_code=201)
def create_run_route(
    request: CreateRunRequest,
    ollama_client: Annotated[OllamaClient, Depends(get_ollama_client)],
    vector_index: Annotated[QdrantVectorIndex, Depends(get_vector_index)],
    reranker: Annotated[PassthroughReranker, Depends(get_reranker)],
    run_store: Annotated[SqliteRunStore, Depends(get_run_store)],
    capability_registry: Annotated[CapabilityRegistry, Depends(get_capability_registry)],
    policy: Annotated[ScopePolicyEngine, Depends(get_policy_engine)],
    workflow_registry: Annotated[WorkflowRegistry, Depends(get_workflow_registry)],
    x_user_id: str | None = Header(default=None),
) -> CreateRunResponse:
    """Create a Run and drive it to completion or a halt.

    Identity resolution mirrors /query and /whoami exactly - the same
    mock IdentityStore, the same X-User-Id header. intake (runs/intake.py)
    is the only place authority is derived from purpose + groups; this
    route builds the IntakeRequest and nothing else, so it cannot hand a
    Run authority policy did not grant.

    keyword_index is fetched via get_keyword_index() directly in this
    function body, not through Depends() - same reasoning as query()'s
    call site: constructing a SqliteKeywordIndex via Depends() is not
    guaranteed to land on the same OS thread it is later used from.
    """
    settings = get_settings()
    identity_store = get_identity_store()
    user_id = x_user_id or settings.default_user
    tenant_id = identity_store.tenant_for(user_id)
    groups = identity_store.groups_for(user_id)

    try:
        workflow_template = workflow_registry.get(request.task_type)
    except WorkflowNotFound as exc:
        raise HTTPException(
            status_code=400,
            detail=f"no workflow registered for task_type {request.task_type!r}",
        ) from exc

    # Dispatch on task_type to build the request-scoped workflow, checked
    # before intake creates (and persists) anything. Every other purpose
    # in config/policies.yaml already has an intake-side profile but no
    # workflow implementation - those are Phases 3-5. This check has to
    # come before run_store.create() below, not after: a task_type that
    # passes the registry lookup above but has no binding here must never
    # leave behind a Run that was created and then abandoned at RECEIVED
    # forever - a real risk once a future phase registers a workflow
    # without updating this dispatch in the same change.
    if request.task_type != "knowledge_qa" or not isinstance(
        workflow_template, KnowledgeQaWorkflow
    ):
        raise HTTPException(
            status_code=400,
            detail=f"task_type {request.task_type!r} has no request-binding implemented yet",
        )

    # Same reasoning as the dispatch check above: validate the
    # task-specific input before creating a Run, not after - a 422 here
    # must not leave an abandoned RECEIVED Run behind.
    question = request.input.get("question")
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(
            status_code=422, detail="input.question is required for knowledge_qa"
        )

    try:
        run = create_run(
            IntakeRequest(
                tenant_id=tenant_id,
                principal_id=user_id,
                groups=groups,
                purpose=request.purpose,
                task_type=request.task_type,
                input_refs=(_input_ref(request.input),),
                budget=_budget_from_override(request.budget),
            ),
            policy,
        )
    except IntakeRejected as exc:
        raise HTTPException(status_code=403, detail=exc.decision.reason) from exc

    run = run_store.create(run)

    ledger = get_effect_ledger()
    audit = JsonlAuditSink(settings.audit_log_path)
    gateway = CapabilityGateway(capability_registry, policy, ledger, audit)

    temperature = (
        request.temperature if request.temperature is not None else settings.ollama_temperature
    )
    workflow = workflow_template.bind(
        KnowledgeQaParams(
            question=question,
            allowed_groups=groups,
            embed_client=ollama_client,
            vector_index=vector_index,
            keyword_index=get_keyword_index(),
            reranker=reranker,
            chat_client=ollama_client,
            temperature=temperature,
        )
    )

    try:
        final_run = execute_workflow(run, workflow, run_store, gateway, ledger)
    except StepExecutionFailed as exc:
        # The Run is already persisted at FAILED_RETRYABLE by the driver.
        # This stays a 500 - the server genuinely failed, and guessing at
        # 503 would be claiming a diagnosis this layer cannot make - but it
        # carries the run_id, without which the durable record the driver
        # just wrote is unreachable by the only caller who wants it.
        raise HTTPException(
            status_code=500,
            detail={
                "error": "workflow step failed",
                "run_id": exc.run.run_id,
                "status": exc.run.status.value,
            },
        ) from exc

    result = None
    step_result = workflow.steps()[0].result
    if final_run.status is RunStatus.COMPLETED and step_result is not None:
        answer = step_result.answer
        result = KnowledgeQaResultResponse(
            answer=answer.answer,
            citations=[
                CitationResponse(chunk_id=c.chunk_id, source=c.source)
                for c in answer.citations
            ],
            abstained=answer.abstained,
            confidence=answer.confidence,
        )

    return CreateRunResponse(run=_to_run_response(final_run), result=result)


@app.get("/runs/{run_id}")
def get_run_route(
    run_id: str,
    run_store: Annotated[SqliteRunStore, Depends(get_run_store)],
    x_user_id: str | None = Header(default=None),
) -> RunResponse:
    """Fetch one Run's durable status. Never the answer/draft content -
    see RunResponse's docstring.

    A Run belonging to another tenant or principal returns 404, the same
    as a run_id that was never created - never 403. A 403 would confirm
    the run_id exists and is just not this caller's, which is itself
    information a caller with no legitimate access to that Run should
    not get.
    """
    settings = get_settings()
    identity_store = get_identity_store()
    user_id = x_user_id or settings.default_user
    tenant_id = identity_store.tenant_for(user_id)

    try:
        run = run_store.get(run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc

    if run.tenant_id != tenant_id or run.principal_id != user_id:
        raise HTTPException(status_code=404, detail="run not found")

    return _to_run_response(run)


def run() -> None:
    uvicorn.run("ekassistant.api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
