"""Typed capability contracts - the vocabulary every tool is described in.

See docs/design/capabilities.md and ADR-0012. A capability is a narrow,
versioned API contract, never just a natural-language description handed
to a model: the contract is what the gateway enforces against, so it has
to state effect class, required scopes, execution zone, data ceiling and
limits as data, not as prose.

Nothing in this module performs I/O or makes an authorization decision.
It is the shared type vocabulary that `registry.py` indexes, `gateway.py`
enforces, `effects.py` binds approvals to, and the mocks in `mocks/`
implement. Keeping it dependency-free is what lets the policy engine and
the Run aggregate both import it without a cycle.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, Protocol

from pydantic import BaseModel


class EffectClass(StrEnum):
    """How much damage an invocation can do, and therefore how it may be
    retried and whether it needs approval.

    Ordered least-to-most consequential in declaration order; use the
    module-level sets below rather than comparing members directly, since
    the interesting predicates ("can I retry this?", "does this need a
    human?") are not the same partition.
    """

    PURE = "PURE"
    READ_ONLY = "READ_ONLY"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    HIGH_IMPACT_WRITE = "HIGH_IMPACT_WRITE"
    IRREVERSIBLE_EXTERNAL = "IRREVERSIBLE_EXTERNAL"


#: Effect classes that may never be re-invoked automatically after an
#: uncertain outcome. A retry here is a second real-world action, so the
#: caller must reconcile the provider's outcome first (see
#: docs/design/capabilities.md#retry-and-reconciliation).
NEVER_BLIND_RETRY = frozenset(
    {EffectClass.HIGH_IMPACT_WRITE, EffectClass.IRREVERSIBLE_EXTERNAL}
)

#: Effect classes that require an approved EffectIntent bound to the exact
#: payload before the gateway will invoke them. REVERSIBLE_WRITE is
#: deliberately NOT in this set - it still requires an intent (so it lands
#: in the ledger and can be compensated), but policy decides whether that
#: intent needs a human. See gateway.invoke().
REQUIRES_APPROVAL = frozenset(
    {EffectClass.HIGH_IMPACT_WRITE, EffectClass.IRREVERSIBLE_EXTERNAL}
)

#: Effect classes that mutate something outside this process and therefore
#: must be recorded in the effect ledger before execution, so an
#: interrupted run can reconcile rather than silently repeat.
REQUIRES_EFFECT_INTENT = frozenset(
    {
        EffectClass.REVERSIBLE_WRITE,
        EffectClass.HIGH_IMPACT_WRITE,
        EffectClass.IRREVERSIBLE_EXTERNAL,
    }
)


class Zone(StrEnum):
    """The execution context a capability runs in.

    Zones are the mechanism behind ADR-0013: private data, untrusted web
    content and outbound external effects must never be reachable from one
    context. A Run carries a set of allowed zones, and the registry will
    not even *show* it a capability outside that set - so a compromised
    model invocation cannot name a tool it was never offered.
    """

    PURE = "PURE"
    INTERNAL_DATA = "INTERNAL_DATA"
    WEB_RESEARCH = "WEB_RESEARCH"
    EFFECT = "EFFECT"


class Classification(IntEnum):
    """Data sensitivity, ordered so a ceiling check is a plain `<=`.

    An IntEnum rather than a StrEnum specifically because the comparison
    is the whole point: a Run's classification_ceiling is the maximum a
    capability may handle, and an unordered enum would push that ordering
    into a lookup table that could drift from the members.
    """

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3


@dataclass(frozen=True)
class Limits:
    """Hard bounds the gateway applies regardless of what a caller asks
    for. Present on every contract - a tool with no stated limits is a
    tool with unbounded blast radius, so the defaults are conservative
    rather than absent.
    """

    timeout_ms: int = 5_000
    max_rows: int = 1_000
    max_bytes: int = 1_048_576


@dataclass(frozen=True)
class ToolContract:
    """The complete, versioned declaration of one capability.

    A frozen dataclass rather than a pydantic model on purpose: this is an
    internal value type (like index/types.py's IndexedChunk), and it holds
    *references to* pydantic models in input_model/output_model rather
    than being one itself.

    `version` is an int and part of the registry key. Changing any
    security-relevant field - effect_class, zone, required_scopes,
    max_classification - is a new version, never an edit in place, so an
    approval recorded against version 2 can never be silently satisfied by
    a differently-scoped version 3.
    """

    tool: str
    version: int
    effect_class: EffectClass
    zone: Zone
    required_scopes: frozenset[str]
    max_classification: Classification
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    limits: Limits = Limits()
    audit_fields: tuple[str, ...] = ("principal_id", "tenant_id", "purpose", "result_hash")
    description: str = ""

    @property
    def ref(self) -> str:
        """Stable "name@version" identifier used in logs, effect intents
        and approvals - so an audit record names the exact contract, not
        just the tool family.
        """
        return f"{self.tool}@{self.version}"


@dataclass(frozen=True)
class Provenance:
    """Where a tool result came from, attached to every ToolResult.

    Required, not optional: the three workflows all end in something a
    human acts on (an email claim, a research report, a reported number),
    and every one of those needs to be traceable to a source without
    re-running the Run.
    """

    source_ref: str
    retrieved_at: float
    content_hash: str
    classification: Classification
    freshness_time: float | None = None


@dataclass(frozen=True)
class ToolResult:
    """What a capability returns, labelled with its own trust level.

    `origin_zone` is the load-bearing field. Anything produced in
    WEB_RESEARCH is untrusted input for everything downstream - it may
    contain instructions aimed at the model - and must never be allowed to
    become a tool name, a recipient, a destination or a query fragment.
    `is_untrusted` exists so that rule is checkable in one place rather
    than re-derived at each call site.
    """

    contract_ref: str
    data: Mapping[str, Any]
    provenance: Provenance
    origin_zone: Zone

    @property
    def is_untrusted(self) -> bool:
        return self.origin_zone is Zone.WEB_RESEARCH


@dataclass(frozen=True)
class InvocationContext:
    """The caller authority the gateway hands down to a capability.

    A capability receives this instead of reaching for ambient credentials
    or a global settings object. That is what makes "authority is carried,
    never generated" (ADR-0012) mechanically true rather than a
    convention: a tool literally has no other way to learn who it is
    acting for.
    """

    run_id: str
    tenant_id: str
    principal_id: str
    purpose: str
    granted_scopes: frozenset[str]
    limits: Limits
    idempotency_key: str | None = None
    deadline_ts: float | None = None


class Capability(Protocol):
    """What every tool implements. One method, one contract.

    `invoke` receives a payload already validated against
    `contract.input_model` by the gateway, so an implementation may assume
    shape - but never authority: it must still act only within the scopes
    named in the InvocationContext it was handed.
    """

    contract: ToolContract

    def invoke(self, payload: Mapping[str, Any], ctx: InvocationContext) -> ToolResult: ...


def canonical_hash(payload: Mapping[str, Any]) -> str:
    """Stable SHA-256 of a JSON-serializable payload.

    Used to bind an approval to an exact effect payload (see
    effects.py). Key order is normalized and separators are fixed so that
    two structurally identical payloads hash identically regardless of how
    they were constructed - otherwise an approval could be invalidated by
    a harmless dict reordering, and reviewers would learn to ignore the
    mismatch, which is worse than not checking at all.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
