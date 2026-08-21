"""The audit sinks themselves.

`JsonlAuditSink` is the sink `POST /runs` wires into its gateway, which
makes it the one that will hold the durable authorization trail the moment
Phase 2 puts a capability call behind an endpoint. Until then no request
path invokes a capability, so that log stays empty in practice - which is
exactly why it needs tests of its own rather than incidental coverage from
a route that never exercises it. A sink that silently fails to serialize an
enum would not be discovered until the first real invocation, which is the
worst possible moment to find out.
"""

import json

import pytest

from ekassistant.capabilities.audit import (
    InMemoryAuditSink,
    InvocationRecord,
    JsonlAuditSink,
)
from ekassistant.capabilities.contracts import EffectClass, Zone


def _record(**overrides) -> InvocationRecord:
    base = dict(
        run_id="run-1",
        tenant_id="acme",
        principal_id="alice",
        purpose="client_communication",
        task_type="email_draft",
        contract_ref="send_email@1",
        effect_class=EffectClass.IRREVERSIBLE_EXTERNAL,
        zone=Zone.EFFECT,
        allowed=False,
        reason="missing scopes ['email.send']",
        policy_version="1",
    )
    base.update(overrides)
    return InvocationRecord(**base)


def test_writes_one_json_line_per_record(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.record(_record(run_id="run-1"))
    sink.record(_record(run_id="run-2"))

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["run_id"] for line in lines] == ["run-1", "run-2"]


def test_creates_the_parent_directory(tmp_path):
    # The configured AUDIT_LOG_PATH may point somewhere that does not
    # exist yet; an audit sink that raises on first write would take the
    # request down with it.
    sink = JsonlAuditSink(tmp_path / "nested" / "deeper" / "audit.jsonl")
    sink.record(_record())
    assert (tmp_path / "nested" / "deeper" / "audit.jsonl").exists()


def test_appends_rather_than_truncating(tmp_path):
    path = tmp_path / "audit.jsonl"
    JsonlAuditSink(path).record(_record(run_id="first"))
    JsonlAuditSink(path).record(_record(run_id="second"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["run_id"] for line in lines] == ["first", "second"]


def test_enums_survive_serialization_as_readable_values(tmp_path):
    # An audit log read during incident review is read by a person, and
    # by whatever grep they reach for first. `EffectClass.__str__` must
    # not degrade to something like "1" or an object repr.
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.record(_record())

    written = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert "IRREVERSIBLE_EXTERNAL" in str(written["effect_class"])
    assert "EFFECT" in str(written["zone"])


def test_a_denial_is_recorded_as_fully_as_an_allow(tmp_path):
    # docs/design/capabilities.md#audit: a stream of denials is itself
    # the signal, so a denied attempt must not be a thinner record.
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.record(_record(allowed=False, reason="missing scopes ['email.send']"))

    written = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert written["allowed"] is False
    assert written["reason"] == "missing scopes ['email.send']"
    assert written["contract_ref"] == "send_email@1"
    assert written["policy_version"] == "1"


def test_no_payload_is_written_only_hashes(tmp_path):
    # The whole point of the record shape: the audit log is one of the
    # most widely readable artefacts here, so it carries hashes and
    # identifiers and never the payload itself.
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.record(_record(payload_hash="abc123", result_hash="def456"))

    written = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert written["payload_hash"] == "abc123"
    assert set(written) == {f for f in InvocationRecord.__dataclass_fields__}


@pytest.mark.parametrize("sink_factory", [lambda p: InMemoryAuditSink(), JsonlAuditSink])
def test_both_sinks_satisfy_the_same_protocol(tmp_path, sink_factory):
    # The mock must not be more permissive than the real thing: whatever
    # a test asserts against InMemoryAuditSink has to hold for the sink
    # actually wired into POST /runs.
    sink = sink_factory(tmp_path / "audit.jsonl")
    sink.record(_record())
