"""Capability audit trail: one record per invocation attempt.

See docs/design/capabilities.md#audit.

Distinct from observability/tracing.py on purpose. A RunTrace answers
"how did this request behave" and is read for debugging and quality
measurement. An InvocationRecord answers "what was this system authorized
to do, and what did it do" - it is read during incident review, and it has
to be complete even for the attempts that were refused, because a stream
of denials is itself the signal.

Records carry hashes and identifiers, never payloads. The audit log is one
of the most widely readable artefacts here; duplicating client email
bodies or CRM fields into it would recreate the sensitive-data problem the
rest of the design is spent avoiding.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from ekassistant.capabilities.contracts import EffectClass, Zone


@dataclass(frozen=True)
class InvocationRecord:
    run_id: str
    tenant_id: str
    principal_id: str
    purpose: str
    task_type: str
    contract_ref: str | None
    effect_class: EffectClass | None
    zone: Zone | None
    allowed: bool
    reason: str
    policy_version: str
    payload_hash: str | None = None
    result_hash: str | None = None
    effect_intent_id: str | None = None
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class AuditSink(Protocol):
    def record(self, record: InvocationRecord) -> None: ...


class InMemoryAuditSink:
    """Mock sink for tests and the Phase 0 skeleton. Keeps records in
    declaration order so a test can assert on the *sequence* of decisions,
    which is usually the interesting part - a denial followed by a
    successful retry under a different tool is a different story from a
    single allow.
    """

    def __init__(self) -> None:
        self.records: list[InvocationRecord] = []

    def record(self, record: InvocationRecord) -> None:
        self.records.append(record)

    def for_run(self, run_id: str) -> list[InvocationRecord]:
        return [r for r in self.records if r.run_id == run_id]


class JsonlAuditSink:
    """Append-only JSON Lines, matching observability/tracing.py's
    file-based V1 approach and relying on the same POSIX O_APPEND
    atomic-write guarantee rather than a lock (see record_run's note).

    Records here are bounded in a way traces are not - every field is an
    identifier, hash, enum or float, with no free text beyond `reason` -
    so the PIPE_BUF size caveat that applies to traces is not a practical
    concern for this file.
    """

    def __init__(self, path: Path):
        self._path = path

    def record(self, record: InvocationRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), default=str) + "\n")
