"""Mock certified-metric lookup. READ_ONLY, INTERNAL_DATA, RESTRICTED.

This is the shape the spreadsheet/database workflow is built on: a small
set of *approved* business metrics with owned definitions, rather than a
model writing queries. The semantic layer owns joins, units and allowed
dimensions; the caller picks a metric_id and dimensions from a declared
set, so an unknown metric is a refusal rather than a plausible-looking
wrong number.

`metric_version` and `freshness_time` are in the output contract, not
optional extras. A number quoted to a client without knowing which
definition produced it or how stale it is has no provenance, and every
downstream validator needs both.
"""

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    InvocationContext,
    Limits,
    ToolContract,
    ToolResult,
    Zone,
)
from ekassistant.capabilities.mocks._shared import mock_result


class MetricInput(BaseModel):
    metric_id: str = Field(min_length=1)
    dimensions: dict[str, str] = Field(default_factory=dict)
    period: str = Field(min_length=1, description="Named period, e.g. '2026-Q2'.")


class MetricOutput(BaseModel):
    metric_id: str
    value: float
    unit: str
    metric_version: str
    source_query_id: str
    period: str
    freshness_time: float


CONTRACT = ToolContract(
    tool="certified_metric",
    version=1,
    effect_class=EffectClass.READ_ONLY,
    zone=Zone.INTERNAL_DATA,
    required_scopes=frozenset({"analytics.metrics.read"}),
    max_classification=Classification.RESTRICTED,
    input_model=MetricInput,
    output_model=MetricOutput,
    limits=Limits(timeout_ms=5_000, max_rows=1_000),
    audit_fields=("principal_id", "tenant_id", "purpose", "metric_id", "result_hash"),
    description="Read one approved business metric over a named period.",
)

# Keyed by (metric_id, period). Only declared combinations resolve - an
# undeclared period is a refusal, not an interpolation, because a
# confidently-returned number for a period nobody certified is worse than
# no number.
_METRICS: dict[tuple[str, str], dict[str, Any]] = {
    ("revenue_by_account", "2026-Q2"): {
        "value": 482_300.0,
        "unit": "USD",
        "metric_version": "3.1",
    },
    ("ticket_acceptance_rate", "2026-Q2"): {
        "value": 0.873,
        "unit": "ratio",
        "metric_version": "2.0",
    },
    ("timeout_rate_by_region", "2026-Q2"): {
        "value": 0.0142,
        "unit": "ratio",
        "metric_version": "1.4",
    },
}


class MockCertifiedMetric:
    contract = CONTRACT

    def invoke(self, payload: Mapping[str, Any], ctx: InvocationContext) -> ToolResult:
        metric_id = payload["metric_id"]
        period = payload["period"]
        entry = _METRICS.get((metric_id, period))
        if entry is None:
            raise KeyError(f"no certified definition for {metric_id!r} over {period!r}")

        query_id = f"q_{abs(hash((metric_id, period))) % 10**8:08d}"
        data = {
            "metric_id": metric_id,
            "period": period,
            "source_query_id": query_id,
            "freshness_time": time.time() - 3_600,
            **entry,
        }
        return mock_result(
            CONTRACT,
            data,
            source_ref=f"semantic://metrics/{metric_id}@{entry['metric_version']}",
            freshness_time=data["freshness_time"],
        )
