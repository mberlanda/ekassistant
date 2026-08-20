"""Run Orchestration: the durable, auditable execution of one user intent.

See docs/design/runs.md and ADR-0011. An agent is a behaviour; a Run is
the record. This subpackage owns state transitions, budgets and
checkpoints - deliberately separate from `orchestration/`, which owns the
V1 single-shot question pipeline, because durable execution state and
model inference fail in completely different ways.
"""
