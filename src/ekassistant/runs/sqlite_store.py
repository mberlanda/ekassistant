"""Durable SQLite RunStore.

See docs/design/runs.md#persistence and ADR-0011. Implements the same
`RunStore` Protocol (runs/store.py) and the same optimistic-concurrency
contract `InMemoryRunStore` already enforces - `ConcurrentRunUpdate` on a
stale save or a duplicate create, `RunNotFound` on a missing get - so
callers written against the mock are written against a contract the real
store actually honours too.

Threading. `SqliteKeywordIndex` has a documented landmine: a sqlite3
connection is only usable from the thread that created it
(`check_same_thread` defaults to True), and FastAPI's `Depends()`
resolution is not guaranteed to land on the same OS thread as the route
handler body - PR #6 reproduced this concretely (16/20 concurrent
requests failing with `sqlite3.ProgrammingError`) and fixed it by
constructing the index inside the handler body so construction and use
share a call stack.

This store sidesteps the same landmine a different way: it never holds a
connection across calls at all. Every method opens its own short-lived
connection, does its work, and closes it before returning - so
construction and use are *always* on the same thread, regardless of which
thread called the method or how many other requests are running
concurrently. The consequence worth knowing: unlike `SqliteKeywordIndex`,
a `SqliteRunStore` instance is safe to share as a singleton across
requests (see api/main.py's `get_run_store`) - there is no
per-request-construction rule to remember here, because the risky part
(the connection) never outlives a single method call in the first place.
Proven under genuine concurrency, not just argued: see
test_run_store_sqlite.py's concurrent-writers test.

Every connection sets WAL journal mode (readers do not block a writer,
and vice versa) and a busy_timeout (a brief writer-writer overlap waits
instead of raising `sqlite3.OperationalError: database is locked`
immediately) - both needed once requests genuinely overlap, not just for
correctness on a single thread.

The `save()` compare-and-swap is done as a single `UPDATE ... WHERE
updated_at <= ?` statement, not a SELECT followed by an UPDATE. Two
threads racing to save the same stale copy must not both observe "not
stale yet" before either writes - SQLite serializes writers, so the
WHERE clause itself is the atomic check, and a plain read-then-write
would reopen exactly the lost-update race this check exists to close.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ekassistant.capabilities.contracts import Classification, Zone
from ekassistant.runs.aggregate import Budget, BudgetSpend, RiskTier, Run, RunStatus, StepResult
from ekassistant.runs.store import ConcurrentRunUpdate, RunNotFound

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    updated_at REAL NOT NULL,
    created_at REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_principal ON runs(tenant_id, principal_id);
"""


def _run_to_json(run: Run) -> str:
    """Serialize a Run to a self-contained JSON blob.

    `run_id`/`created_at`/`updated_at` are duplicated into their own
    indexed columns for query performance - but always written from the
    same `Run` in the same statement as this blob, so the columns and the
    blob can never drift apart the way a separately-maintained cache
    could.
    """
    payload = {
        "run_id": run.run_id,
        "tenant_id": run.tenant_id,
        "principal_id": run.principal_id,
        "purpose": run.purpose,
        "task_type": run.task_type,
        "approved_scopes": sorted(run.approved_scopes),
        "allowed_zones": sorted(zone.value for zone in run.allowed_zones),
        "classification_ceiling": int(run.classification_ceiling),
        "risk_tier": run.risk_tier.value,
        "plan_version": run.plan_version,
        "prompt_version": run.prompt_version,
        "model_route": run.model_route,
        "policy_version": run.policy_version,
        "status": run.status.value,
        "terminal_reason": run.terminal_reason,
        "checkpoint": run.checkpoint,
        "step_results": [
            {
                "step_name": s.step_name,
                "contract_ref": s.contract_ref,
                "ok": s.ok,
                "evidence_ref": s.evidence_ref,
                "detail": s.detail,
                "started_at": s.started_at,
                "duration_ms": s.duration_ms,
            }
            for s in run.step_results
        ],
        "input_refs": list(run.input_refs),
        "evidence_refs": list(run.evidence_refs),
        "effect_intent_ids": list(run.effect_intent_ids),
        "budget": {
            "max_steps": run.budget.max_steps,
            "max_tokens": run.budget.max_tokens,
            "max_spend_micros": run.budget.max_spend_micros,
            "deadline_ts": run.budget.deadline_ts,
        },
        "spend": {
            "steps": run.spend.steps,
            "tokens": run.spend.tokens,
            "spend_micros": run.spend.spend_micros,
        },
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
    return json.dumps(payload, sort_keys=True)


def _run_from_json(blob: str) -> Run:
    d = json.loads(blob)
    return Run(
        tenant_id=d["tenant_id"],
        principal_id=d["principal_id"],
        purpose=d["purpose"],
        task_type=d["task_type"],
        approved_scopes=frozenset(d["approved_scopes"]),
        allowed_zones=frozenset(Zone(z) for z in d["allowed_zones"]),
        classification_ceiling=Classification(d["classification_ceiling"]),
        risk_tier=RiskTier(d["risk_tier"]),
        plan_version=d["plan_version"],
        prompt_version=d["prompt_version"],
        model_route=d["model_route"],
        policy_version=d["policy_version"],
        status=RunStatus(d["status"]),
        terminal_reason=d["terminal_reason"],
        checkpoint=d["checkpoint"],
        step_results=tuple(
            StepResult(
                step_name=s["step_name"],
                contract_ref=s["contract_ref"],
                ok=s["ok"],
                evidence_ref=s["evidence_ref"],
                detail=s["detail"],
                started_at=s["started_at"],
                duration_ms=s["duration_ms"],
            )
            for s in d["step_results"]
        ),
        input_refs=tuple(d["input_refs"]),
        evidence_refs=tuple(d["evidence_refs"]),
        effect_intent_ids=tuple(d["effect_intent_ids"]),
        budget=Budget(**d["budget"]),
        spend=BudgetSpend(**d["spend"]),
        run_id=d["run_id"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


class SqliteRunStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create(self, run: Run) -> Run:
        try:
            with self._session() as conn:
                conn.execute(
                    "INSERT INTO runs (run_id, tenant_id, principal_id, updated_at, "
                    "created_at, data) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        run.tenant_id,
                        run.principal_id,
                        run.updated_at,
                        run.created_at,
                        _run_to_json(run),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConcurrentRunUpdate(f"run {run.run_id} already exists") from exc
        return run

    def get(self, run_id: str) -> Run:
        with self._session() as conn:
            row = conn.execute("SELECT data FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(run_id)
        return _run_from_json(row[0])

    def save(self, run: Run) -> Run:
        with self._session() as conn:
            cur = conn.execute(
                "UPDATE runs SET data = ?, updated_at = ?, tenant_id = ?, principal_id = ? "
                "WHERE run_id = ? AND updated_at <= ?",
                (
                    _run_to_json(run),
                    run.updated_at,
                    run.tenant_id,
                    run.principal_id,
                    run.run_id,
                    run.updated_at,
                ),
            )
            if cur.rowcount == 0:
                existing = conn.execute(
                    "SELECT updated_at FROM runs WHERE run_id = ?", (run.run_id,)
                ).fetchone()
                if existing is None:
                    raise RunNotFound(run.run_id)
                raise ConcurrentRunUpdate(
                    f"run {run.run_id} was modified at {existing[0]}, "
                    f"later than this copy's {run.updated_at}"
                )
        return run

    def list_for_principal(self, tenant_id: str, principal_id: str) -> list[Run]:
        # Tenant is part of the WHERE clause, not a post-filter applied to
        # an unscoped SELECT - a caller that forgets to pass it gets
        # nothing rather than everything, matching InMemoryRunStore.
        with self._session() as conn:
            rows = conn.execute(
                "SELECT data FROM runs WHERE tenant_id = ? AND principal_id = ? "
                "ORDER BY created_at ASC",
                (tenant_id, principal_id),
            ).fetchall()
        return [_run_from_json(row[0]) for row in rows]
