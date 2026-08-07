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
        abstained=False,
        citation_count=1,
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
    assert record["chat_model"] == "llama3.2:1b"
    assert record["embed_model"] == "nomic-embed-text"
    assert "timestamp" in record
