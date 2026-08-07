"""CLI entry point: run the eval harness against the live services.

See docs/design/observability.md and docs/roadmap.md item 8. Invoked via
`make eval` / the `ekassistant-eval` console script.
"""

from ekassistant.config.settings import get_settings
from ekassistant.identity.store import IdentityStore
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.vector_index import QdrantVectorIndex
from ekassistant.models.ollama_client import OllamaClient
from ekassistant.observability.eval_harness import (
    EVAL_QUERIES,
    build_retrieval_checks,
    measure_generation,
    run_retrieval_checks,
)
from ekassistant.retrieval.reranker import PassthroughReranker


def run() -> None:
    settings = get_settings()
    identities = IdentityStore.from_yaml(settings.identities_path)
    vector_index = QdrantVectorIndex(settings)
    vector_index.ensure_collection()
    keyword_index = SqliteKeywordIndex(settings)
    ollama_client = OllamaClient(settings)
    reranker = PassthroughReranker()

    print("=== Tier 1: retrieval/ACL correctness (deterministic) ===")
    checks = build_retrieval_checks(settings.seed_corpus_manifest, identities)
    if not checks:
        # An empty check list would otherwise print "0/0 passed" and exit
        # 0 - a silent no-op that looks identical to "everything passed".
        # A mismatch between EVAL_QUERIES and seed_corpus/manifest.yaml
        # (e.g. every source renamed) is exactly the kind of drift this
        # harness exists to catch, not paper over.
        raise SystemExit(
            "No retrieval checks were built - EVAL_QUERIES and "
            f"{settings.seed_corpus_manifest} have no overlapping sources."
        )
    results = run_retrieval_checks(
        checks, identities, ollama_client, vector_index, keyword_index, reranker
    )
    failures = [r for r in results if not r.passed]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.check.name}")
        if not result.passed:
            print(f"         expected_source={result.check.expected_source!r}")
            print(f"         retrieved_sources={result.retrieved_sources!r}")
    print(f"{len(results) - len(failures)}/{len(results)} retrieval checks passed.")

    print("\n=== Tier 2: generation quality (measurement, not pass/fail) ===")
    # The VPN policy question specifically - it's the one docs/roadmap.md
    # already has documented live behavior for (llama3.2:1b's variance).
    question = EVAL_QUERIES["remote-access-vpn-policy.md"]
    stats = measure_generation(
        question,
        identities.groups_for("alice"),
        runs=5,
        embed_client=ollama_client,
        vector_index=vector_index,
        keyword_index=keyword_index,
        reranker=reranker,
        chat_client=ollama_client,
    )
    print(f"  question: {question!r} (as alice)")
    print(f"  runs: {stats.runs}")
    print(f"  abstain rate: {stats.abstain_rate:.0%} ({stats.abstain_count}/{stats.runs})")
    for reason, count in sorted(stats.abstain_reason_counts.items()):
        print(f"    - {reason}: {count}")
    print(f"  avg retrieval latency: {stats.avg_retrieval_ms:.0f}ms")
    print(f"  avg generation latency: {stats.avg_generation_ms:.0f}ms")

    if failures:
        raise SystemExit(f"{len(failures)} retrieval/ACL check(s) failed - see above.")


if __name__ == "__main__":
    run()
