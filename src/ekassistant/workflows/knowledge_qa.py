"""`knowledge_qa`: V1's question-answering pipeline, re-expressed as a
workflow template.

See docs/design/runs.md#workflows and the roadmap's Phase 1 entry. The
point of this module is not new behaviour - `orchestration/pipeline.py`'s
`answer_question()` is untouched and `POST /query` keeps calling it
directly, byte-identical, as V1 always did. The point is that RAG is now
*one task type a Run can declare*, resolved through the same
`WorkflowRegistry` and driven through the same `runs/service.py` state
machine that every future workflow (email drafting, market research,
analytics) will use - not a special case with its own endpoint logic.

Why this step does not call `ctx.gateway`. A "pure" reading of
`workflows/base.py` would have every external touch go through a
registered capability. Retrieval and generation are not registered
capabilities today - `capabilities/bootstrap.py`'s catalogue has no
"search the knowledge base" or "call the chat model" entry, and adding
one is real, unplanned work (a contract, pydantic input/output models, a
mock, a policy scope, registration-time checks) that belongs with
"Phase 2 - Gateway live", not this phase. So this step calls
`answer_question()` directly, exactly as `api/main.py`'s `/query` route
already does, and ACL enforcement continues to happen exactly where it
always has - inside the index adapters (ADR-0002) - not via the
capability gateway. `ctx.gateway` and `ctx.ledger` are still threaded
through per `WorkflowContext`'s shape, unused by this step, so a future
step that *does* need a capability slots in without a signature change.

Why `bind()` exists. `WorkflowRegistry.register()` takes one instance and
the `Workflow` Protocol's `steps()` takes no arguments, but this step
needs per-request data (the question, the caller's allowed groups, which
model clients to use) that a shared registry entry cannot hold - two
concurrent requests would race to overwrite each other's question on a
mutable shared instance, and a frozen instance can't hold it at all. So
the registry holds one unbound template (used for task_type/version
lookup and `check_sufficiency()`, which only needs the static
`required_zones`/`required_scopes` declared below); the API route calls
`.bind(params)` to get a fresh, request-scoped instance whose `steps()`
actually runs. Calling `.steps()` on an unbound template is a
programming error, not a runtime condition a caller should branch on -
hence the `RuntimeError`.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ekassistant.capabilities.contracts import Zone
from ekassistant.models.embedder import Embedder
from ekassistant.models.generation import DEFAULT_TEMPERATURE, ChatClient
from ekassistant.orchestration.pipeline import PipelineResult, answer_question
from ekassistant.retrieval.reranker import Reranker
from ekassistant.retrieval.retriever import KeywordSearcher, VectorSearcher
from ekassistant.workflows.base import StepOutcome, StepSpec, WorkflowContext


@dataclass(frozen=True)
class KnowledgeQaParams:
    """Everything one `knowledge_qa` Run needs that the Run itself does
    not (and per docs/design/runs.md, must not) carry - the question
    text and the concrete infra clients, mirroring exactly what
    `api/main.py`'s `/query` route already assembles for
    `answer_question()`.
    """

    question: str
    allowed_groups: list[str]
    embed_client: Embedder
    vector_index: VectorSearcher
    keyword_index: KeywordSearcher
    reranker: Reranker
    chat_client: ChatClient
    temperature: float = DEFAULT_TEMPERATURE


class KnowledgeQaStep:
    spec = StepSpec(name="answer_question")

    def __init__(self, params: KnowledgeQaParams):
        self._params = params
        #: The full pipeline result, kept on the step instance - never on
        #: the Run - so the API route can echo the answer back to the
        #: caller once, synchronously, without writing draft/answer
        #: content into the durable Run record. See
        #: docs/design/runs.md#what-a-run-does-not-store: the Run is read
        #: by a much wider audience (status polling, audit, operations)
        #: than the one caller who asked this question and is owed the
        #: answer.
        self.result: PipelineResult | None = None

    def execute(self, ctx: WorkflowContext) -> StepOutcome:
        p = self._params
        self.result = answer_question(
            question=p.question,
            allowed_groups=p.allowed_groups,
            embed_client=p.embed_client,
            vector_index=p.vector_index,
            keyword_index=p.keyword_index,
            reranker=p.reranker,
            chat_client=p.chat_client,
            temperature=p.temperature,
        )
        answer = self.result.answer
        detail = (
            f"abstained: {answer.reason}"
            if answer.abstained
            else f"answered with {len(answer.citations)} citation(s)"
        )
        return StepOutcome(
            run=ctx.run,
            ok=True,
            detail=detail,
            evidence_refs=tuple(self.result.retrieved_chunk_ids),
        )


class KnowledgeQaWorkflow:
    """Template for `task_type="knowledge_qa"`.

    `required_scopes` names `kb.documents.read` - a scope
    `config/policies.yaml` already grants to `all-staff` and
    `engineering` but that, before this workflow existed, nothing ever
    checked. `guest` (see config/identities.yaml) holds no groups and
    therefore no scopes, so a `guest` Run declaring this task type is
    refused by `check_sufficiency()` before a single step runs - a
    capability-scope deny-path that is new with this workflow, distinct
    from (and in addition to) V1's per-chunk ACL enforcement, which still
    happens exactly as before inside the index adapters.
    """

    task_type = "knowledge_qa"
    version = 1
    required_zones = frozenset({Zone.INTERNAL_DATA})
    required_scopes = frozenset({"kb.documents.read"})

    def __init__(self, params: KnowledgeQaParams | None = None):
        self._params = params
        # Built once, here, rather than inside steps() - steps() is called
        # more than once in a single execute_workflow() pass in general
        # (this workflow only has one step, but the shape must not rely on
        # that), and the API route reads .result off this exact instance
        # after execution to build the HTTP response. A fresh
        # KnowledgeQaStep per steps() call would silently hand the route
        # an object that was never executed.
        self._step = KnowledgeQaStep(params) if params is not None else None

    def bind(self, params: KnowledgeQaParams) -> "KnowledgeQaWorkflow":
        return KnowledgeQaWorkflow(params)

    def steps(self) -> Sequence[KnowledgeQaStep]:
        if self._step is None:
            raise RuntimeError(
                "KnowledgeQaWorkflow.steps() called on the unbound template - "
                "call .bind(params) first to get a request-scoped instance"
            )
        return [self._step]
