"""Two-tier eval harness. See docs/design/observability.md.

Tier 1 (deterministic, correctness): for each seed_corpus document and
each mock user, checks whether retrieval finds that document's chunks
when it should (the user's groups overlap the document's ACL) and does
NOT find them when it shouldn't - this is the ACL test case class
docs/design/observability.md names explicitly. No LLM involved, so a
failure here is a real regression, never model noise.

Tier 2 (measurement, not assertion): runs the *full* pipeline, including
generation, for a fixed answerable question repeated N times, and reports
the abstain rate and citation-validity rate as numbers, not a pass/fail.
This is deliberate - docs/roadmap.md already documents that llama3.2:1b
is measurably unreliable at the cite-or-abstain contract (confirmed live
in earlier PRs), so asserting "must answer" here would make the eval
suite flaky for a reason that has nothing to do with a real regression.
Tier 2 exists to make that variability visible and trackable over time
(e.g. across a model swap, per ADR-0004), not to assert it away.

EVAL_QUERIES couples eval cases to specific seed_corpus content by
design (see docs/roadmap.md item 3's connector test for the same
pattern) - if seed_corpus content changes enough that a query no longer
matches its target document, this will start failing loudly, which is
the intended signal to update the query, not something to guard against
happening.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from ekassistant.identity.store import IdentityStore
from ekassistant.models.context import ContextChunk
from ekassistant.models.embedder import Embedder
from ekassistant.models.generation import ChatClient, generate_answer
from ekassistant.retrieval.reranker import Reranker
from ekassistant.retrieval.retriever import KeywordSearcher, VectorSearcher, retrieve

EVAL_QUERIES: dict[str, str] = {
    "oncall-runbook.md": "How do I restart the deploy pipeline?",
    "expense-approval-policy.md": "What is the expense approval threshold?",
    "support-escalation-macros.md": "How do refund macros work for support tickets?",
    "remote-access-vpn-policy.md": "Does the VPN require multi-factor authentication?",
    "engineering-onboarding.html": "What are first week expectations for new engineers?",
    "vendor-contract-terms.pdf": "What are the vendor contract termination terms?",
    "customer-escalation-policy.docx": "What are the retention offer rules?",
}


@dataclass(frozen=True)
class RetrievalCheck:
    name: str
    user_id: str
    query: str
    expected_source: str
    should_find: bool


@dataclass(frozen=True)
class RetrievalCheckResult:
    check: RetrievalCheck
    passed: bool
    retrieved_sources: list[str]


def build_retrieval_checks(
    manifest_path: Path, identities: IdentityStore
) -> list[RetrievalCheck]:
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    checks: list[RetrievalCheck] = []
    for source, meta in manifest.items():
        query = EVAL_QUERIES.get(source)
        if query is None:
            continue
        allowed_groups = set(meta.get("allowed_groups", []))
        for user_id in identities.all_user_ids():
            has_access = bool(allowed_groups & set(identities.groups_for(user_id)))
            checks.append(
                RetrievalCheck(
                    name=f"{user_id} {'can' if has_access else 'cannot'} find {source}",
                    user_id=user_id,
                    query=query,
                    expected_source=source,
                    should_find=has_access,
                )
            )
    return checks


def run_retrieval_checks(
    checks: list[RetrievalCheck],
    identities: IdentityStore,
    embed_client: Embedder,
    vector_index: VectorSearcher,
    keyword_index: KeywordSearcher,
    reranker: Reranker,
) -> list[RetrievalCheckResult]:
    results = []
    for check in checks:
        chunks = retrieve(
            check.query,
            identities.groups_for(check.user_id),
            embed_client,
            vector_index,
            keyword_index,
            reranker,
            # Wider than production's default final_k=5: this tier checks
            # presence/absence (is the document reachable at all under
            # this user's ACL), not ranking quality (is it #1) - a real
            # ranking-quality regression that pushes a correct document
            # from rank 1 to rank 8 wouldn't be this tier's job to catch.
            final_k=10,
        )
        retrieved_sources = [c.source for c in chunks]
        found = check.expected_source in retrieved_sources
        results.append(
            RetrievalCheckResult(
                check=check,
                passed=(found == check.should_find),
                retrieved_sources=retrieved_sources,
            )
        )
    return results


@dataclass(frozen=True)
class GenerationStats:
    runs: int
    abstain_count: int
    abstain_reason_counts: dict[str, int]
    avg_retrieval_ms: float
    avg_generation_ms: float

    @property
    def abstain_rate(self) -> float:
        return self.abstain_count / self.runs if self.runs else 0.0


def measure_generation(
    question: str,
    allowed_groups: list[str],
    runs: int,
    embed_client: Embedder,
    vector_index: VectorSearcher,
    keyword_index: KeywordSearcher,
    reranker: Reranker,
    chat_client: ChatClient,
) -> GenerationStats:
    abstain_count = 0
    abstain_reason_counts: dict[str, int] = {}
    retrieval_times: list[float] = []
    generation_times: list[float] = []

    for _ in range(runs):
        retrieval_start = time.monotonic()
        context_chunks: list[ContextChunk] = retrieve(
            question, allowed_groups, embed_client, vector_index, keyword_index, reranker
        )
        retrieval_times.append((time.monotonic() - retrieval_start) * 1000)

        generation_start = time.monotonic()
        answer = generate_answer(question, context_chunks, chat_client)
        generation_times.append((time.monotonic() - generation_start) * 1000)

        if answer.abstained:
            abstain_count += 1
            reason = answer.reason or "unknown"
            abstain_reason_counts[reason] = abstain_reason_counts.get(reason, 0) + 1
        else:
            # generate_answer() guarantees a non-abstained answer always
            # has >=1 valid citation. This is a canary, not a metric: if
            # it ever fires, that's a real regression in that guarantee,
            # not something to silently tally and report as a number
            # alongside the abstain-reason breakdown above.
            assert answer.citations, (
                "generate_answer() returned a non-abstained answer with no "
                "citations - this violates its documented cite-or-abstain contract"
            )

    return GenerationStats(
        runs=runs,
        abstain_count=abstain_count,
        abstain_reason_counts=abstain_reason_counts,
        avg_retrieval_ms=sum(retrieval_times) / len(retrieval_times) if retrieval_times else 0.0,
        avg_generation_ms=sum(generation_times) / len(generation_times)
        if generation_times
        else 0.0,
    )
