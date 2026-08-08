import concurrent.futures
import json

from ekassistant.observability.tracing import RunTrace, record_run


def _trace(**overrides) -> RunTrace:
    defaults = dict(
        user_id="alice",
        groups=["engineering", "all-staff"],
        question="does the vpn need mfa?",
        retrieved_chunk_ids=["c1", "c2"],
        retrieval_ms=12.5,
        generation_ms=340.2,
        temperature=0.2,
        abstained=False,
        abstain_reason=None,
        citation_count=1,
        confidence=0.9,
        chat_model="llama3.2:1b",
        embed_model="nomic-embed-text",
    )
    defaults.update(overrides)
    return RunTrace(**defaults)


def test_record_run_appends_one_json_line(tmp_path):
    path = tmp_path / "traces.jsonl"

    record_run(_trace(), path)
    record_run(_trace(user_id="bob"), path)

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["user_id"] == "alice"
    assert json.loads(lines[1])["user_id"] == "bob"


def test_record_run_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "traces.jsonl"

    record_run(_trace(), path)

    assert path.exists()


def test_recorded_trace_contains_the_fields_observability_design_asks_for(tmp_path):
    path = tmp_path / "traces.jsonl"

    record_run(_trace(), path)

    record = json.loads(path.read_text().splitlines()[0])
    # docs/design/observability.md's Metrics section: latency per stage,
    # retrieval hit counts, abstain rate (derivable from `abstained`
    # across many records), model names for the prompt/version registry.
    assert record["retrieval_ms"] == 12.5
    assert record["generation_ms"] == 340.2
    assert record["retrieved_chunk_ids"] == ["c1", "c2"]
    assert record["abstained"] is False
    assert record["abstain_reason"] is None
    assert record["chat_model"] == "llama3.2:1b"
    assert record["embed_model"] == "nomic-embed-text"
    assert "timestamp" in record


def test_abstain_reason_distinguishes_from_a_plain_abstain_rate(tmp_path):
    # The whole point of adding abstain_reason: "abstain rate" and
    # "citation-validation failure rate" are two distinct metrics per
    # docs/design/observability.md, and a trace record needs to carry
    # enough to compute both, not just a single collapsed boolean.
    path = tmp_path / "traces.jsonl"

    record_run(_trace(abstained=True, abstain_reason="invalid_citation"), path)

    record = json.loads(path.read_text().splitlines()[0])
    assert record["abstained"] is True
    assert record["abstain_reason"] == "invalid_citation"


def test_concurrent_writes_do_not_corrupt_or_lose_lines(tmp_path):
    # Real requests to POST /query are genuinely concurrent (see the
    # SQLite thread-affinity fix elsewhere in this PR series) - pins the
    # atomic-append guarantee record_run()'s docstring documents: every
    # write shows up, and every line is independently valid JSON, with
    # no interleaved/corrupted lines from concurrent writers.
    path = tmp_path / "traces.jsonl"

    def write_one(i: int) -> None:
        record_run(_trace(user_id=f"user{i}"), path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(write_one, range(100)))

    lines = path.read_text().splitlines()
    assert len(lines) == 100
    user_ids = {json.loads(line)["user_id"] for line in lines}
    assert user_ids == {f"user{i}" for i in range(100)}
