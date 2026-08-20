"""Helpers shared by the mock capabilities.

Mirrors ingest/parsers/_shared.py's role: the small amount of logic every
sibling needs, in one place, rather than copied five times and drifting.
"""

import time
from collections.abc import Mapping
from typing import Any

from ekassistant.capabilities.contracts import (
    Classification,
    Provenance,
    ToolContract,
    ToolResult,
    canonical_hash,
)


def mock_result(
    contract: ToolContract,
    data: Mapping[str, Any],
    *,
    source_ref: str,
    classification: Classification | None = None,
    freshness_time: float | None = None,
) -> ToolResult:
    """Build a ToolResult with provenance filled in consistently.

    `classification` defaults to the contract's own ceiling rather than to
    PUBLIC. Defaulting downward would mean a mock that forgets to label
    its output produces data that looks safer than it is, and the
    conservative direction is the only defensible default here.
    """
    return ToolResult(
        contract_ref=contract.ref,
        data=dict(data),
        provenance=Provenance(
            source_ref=source_ref,
            retrieved_at=time.time(),
            content_hash=canonical_hash(data),
            classification=(
                contract.max_classification if classification is None else classification
            ),
            freshness_time=freshness_time,
        ),
        origin_zone=contract.zone,
    )
