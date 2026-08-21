import pathlib

import pytest
import yaml

from ekassistant.identity.store import IdentityStore
from ekassistant.index.types import SearchResult
from ekassistant.models.schema import GroundedAnswer
from ekassistant.observability import eval_harness
from ekassistant.observability.eval_harness import (
    EVAL_QUERIES,
    RetrievalCheck,
    build_retrieval_checks,
    measure_generation,
    run_retrieval_checks,
)
from ekassistant.retrieval.reranker import PassthroughReranker

REPO_ROOT = pathlib.Path(__file__).parent.parent


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]


class FakeVectorIndex:
    def search(self, query_embedding, allowed_groups, top_n):
        return []


class FakeKeywordIndexBySource:
    """Returns a hit for a given source only if the caller's groups
    intersect that source's configured ACL - models a real ACL-correct
    index without needing live Qdrant/SQLite.
    """

    def __init__(self, acl_by_source: dict[str, list[str]]):
        self._acl_by_source = acl_by_source

    def search(self, query_text, allowed_groups, top_n):
        results = []
        for source, allowed in self._acl_by_source.items():
            if set(allowed) & set(allowed_groups):
                results.append(
                    SearchResult(chunk_id=f"{source}#0", source=source, text="x", score=1.0)
                )
        return results


class FakeChatClient:
    def __init__(self, responses: list[str]):
        self._responses = iter(responses)

    def chat_json(self, system: str, user: str, json_schema: dict, temperature: float) -> str:
        return next(self._responses)


def _identities() -> IdentityStore:
    return IdentityStore(
        {
            "alice": ["engineering"],
            "carol": ["finance"],
            "guest": [],
        }
    )


def test_build_retrieval_checks_covers_every_user_and_document(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "doc-a.md:\n  allowed_groups: [engineering]\n"
        "doc-b.md:\n  allowed_groups: [finance]\n"
    )
    original_queries = dict(EVAL_QUERIES)
    EVAL_QUERIES["doc-a.md"] = "question about a"
    EVAL_QUERIES["doc-b.md"] = "question about b"
    try:
        checks = build_retrieval_checks(manifest, _identities())
    finally:
        EVAL_QUERIES.clear()
        EVAL_QUERIES.update(original_queries)

    def _find(user_id: str, source: str) -> RetrievalCheck:
        return next(c for c in checks if c.user_id == user_id and c.expected_source == source)

    # 2 documents x 3 users = 6 checks
    assert len(checks) == 6
    assert _find("alice", "doc-a.md").should_find is True
    assert _find("alice", "doc-b.md").should_find is False
    assert _find("guest", "doc-a.md").should_find is False


def test_build_retrieval_checks_skips_documents_with_no_eval_query(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("no-query-defined-for-this.md:\n  allowed_groups: [engineering]\n")

    checks = build_retrieval_checks(manifest, _identities())

    assert checks == []


def test_run_retrieval_checks_passes_when_acl_correctly_grants_access():
    check = RetrievalCheck(
        name="alice can find doc-a.md",
        user_id="alice",
        query="question",
        expected_source="doc-a.md",
        should_find=True,
    )
    keyword_index = FakeKeywordIndexBySource({"doc-a.md": ["engineering"]})

    results = run_retrieval_checks(
        [check], _identities(), FakeEmbedder(), FakeVectorIndex(), keyword_index,
        PassthroughReranker(),
    )

    assert results[0].passed is True


def test_run_retrieval_checks_fails_when_acl_incorrectly_leaks_content():
    # Simulates a real ACL bug: guest should NOT find doc-a.md, but the
    # fake index here (deliberately misconfigured) returns it anyway.
    check = RetrievalCheck(
        name="guest cannot find doc-a.md",
        user_id="guest",
        query="question",
        expected_source="doc-a.md",
        should_find=False,
    )

    class BrokenKeywordIndex:
        def search(self, query_text, allowed_groups, top_n):
            return [SearchResult(chunk_id="doc-a.md#0", source="doc-a.md", text="x", score=1.0)]

    results = run_retrieval_checks(
        [check], _identities(), FakeEmbedder(), FakeVectorIndex(), BrokenKeywordIndex(),
        PassthroughReranker(),
    )

    assert results[0].passed is False


def test_measure_generation_reports_abstain_rate():
    responses = [
        '{"answer": "Yes.", '
        '"citations": [{"index": 1}], '
        '"abstained": false, "confidence": 0.75}',
        '{"answer": "", "citations": [], "abstained": true, "confidence": 0.1}',
    ]
    keyword_index = FakeKeywordIndexBySource({"doc-a.md": ["engineering"]})

    stats = measure_generation(
        "question",
        ["engineering"],
        runs=2,
        embed_client=FakeEmbedder(),
        vector_index=FakeVectorIndex(),
        keyword_index=keyword_index,
        reranker=PassthroughReranker(),
        chat_client=FakeChatClient(responses),
    )

    assert stats.runs == 2
    assert stats.abstain_count == 1
    assert stats.abstain_rate == 0.5
    assert stats.abstain_reason_counts == {"model_reported_abstain": 1}


def test_measure_generation_asserts_on_a_citation_contract_violation(monkeypatch):
    # generate_answer() itself prevents a non-abstained, citation-less
    # GroundedAnswer by construction (see test_generation.py) - this
    # test proves measure_generation()'s canary assertion would actually
    # fire if that guarantee were ever broken, rather than silently
    # passing an invalid result through as if it were a normal answer.
    monkeypatch.setattr(
        eval_harness,
        "generate_answer",
        lambda question, context_chunks, chat_client: GroundedAnswer(
            answer="Yes.", citations=[], abstained=False, reason=None
        ),
    )
    keyword_index = FakeKeywordIndexBySource({"doc-a.md": ["engineering"]})

    with pytest.raises(AssertionError):
        measure_generation(
            "question",
            ["engineering"],
            runs=1,
            embed_client=FakeEmbedder(),
            vector_index=FakeVectorIndex(),
            keyword_index=keyword_index,
            reranker=PassthroughReranker(),
            chat_client=FakeChatClient(["irrelevant"]),
        )


def test_eval_queries_reference_documents_that_exist_in_the_real_seed_corpus():
    """EVAL_QUERIES is hand-maintained and could drift from seed_corpus/
    (see the module docstring) - this test makes that drift loud instead
    of silent.
    """
    manifest_path = REPO_ROOT / "seed_corpus" / "manifest.yaml"
    manifest_sources = set(yaml.safe_load(manifest_path.read_text()) or {})

    for source in EVAL_QUERIES:
        assert source in manifest_sources, (
            f"EVAL_QUERIES references {source!r}, not found in seed_corpus/manifest.yaml"
        )
