"""The Run aggregate: identity, authority, budget and progress in one record.

See docs/design/runs.md and ADR-0011.

A Run is a frozen dataclass and every mutation returns a new instance.
That is a deliberate cost: it means callers must thread the returned Run
through, and it means the store writes whole snapshots. What it buys is
that a Run handed to a workflow step cannot be widened by that step -
`approved_scopes`, `allowed_zones` and `classification_ceiling` are the
authority the capability gateway checks against, and if a step could
mutate them in place, "authority is carried, never generated" (ADR-0012)
would be enforced only by everyone remembering not to.
"""

import time
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ekassistant.capabilities.contracts import Classification, Zone


class RunStatus(StrEnum):
    """States from docs/design/runs.md's state machine.

    The happy path is RECEIVED -> CLARIFYING -> PLANNED -> RUNNING ->
    VALIDATING -> AWAITING_APPROVAL -> COMMITTING_EFFECT -> COMPLETED,
    with CLARIFYING and the two approval/commit states skippable for runs
    that need neither.
    """

    RECEIVED = "RECEIVED"
    CLARIFYING = "CLARIFYING"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMMITTING_EFFECT = "COMMITTING_EFFECT"
    COMPLETED = "COMPLETED"

    REJECTED_POLICY = "REJECTED_POLICY"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TIMED_OUT = "TIMED_OUT"


class RiskTier(StrEnum):
    """Set at intake, not by the model. Drives how much validation and
    approval a Run's output needs; a tier can be raised mid-run (an
    external recipient appears, a restricted document is retrieved) but
    never lowered, see `Run.raise_risk_tier`.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class Budget:
    """Termination guarantees. A Run without a budget is a Run that can
    spin forever on a retry loop, so every Run gets one - the defaults are
    small on purpose, sized for the local models this project runs
    against (ADR-0004).

    `deadline_ts` is an absolute wall-clock timestamp rather than a
    duration because a Run can be checkpointed and resumed minutes later;
    a duration would silently reset the clock on every resume.
    """

    max_steps: int = 20
    max_tokens: int = 100_000
    max_spend_micros: int = 0
    deadline_ts: float | None = None


@dataclass(frozen=True)
class BudgetSpend:
    """What a Run has consumed so far. Separate from Budget so the limit
    and the usage can be compared, logged and reasoned about
    independently.
    """

    steps: int = 0
    tokens: int = 0
    spend_micros: int = 0


@dataclass(frozen=True)
class StepResult:
    """One completed workflow step, kept so a resumed Run does not repeat
    work. Holds a *reference* to any evidence rather than the payload
    itself - see docs/design/runs.md#what-a-run-does-not-store: the Run
    record is widely read (status polling, audit, operations) and must not
    become a second, less-guarded copy of sensitive content.
    """

    step_name: str
    contract_ref: str | None
    ok: bool
    evidence_ref: str | None = None
    detail: str | None = None
    started_at: float = 0.0
    duration_ms: float = 0.0


def _new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class Run:
    """One user intent, durably. Field groups match docs/design/runs.md's
    minimal schema table.

    Note what is absent: no raw question text beyond `input_refs`, no
    retrieved content, no draft body. Those live in their own stores and
    are referenced. See StepResult's note for why.
    """

    # Identity and tenancy
    tenant_id: str
    principal_id: str

    # Authority - the fields the capability gateway intersects against
    purpose: str
    task_type: str
    approved_scopes: frozenset[str] = frozenset()
    allowed_zones: frozenset[Zone] = frozenset()
    classification_ceiling: Classification = Classification.PUBLIC

    # Routing and replay
    risk_tier: RiskTier = RiskTier.LOW
    plan_version: str = "0"
    prompt_version: str = "0"
    model_route: str = ""
    policy_version: str = "0"

    # Progress
    status: RunStatus = RunStatus.RECEIVED
    terminal_reason: str | None = None
    checkpoint: str | None = None
    step_results: tuple[StepResult, ...] = ()

    # Lineage - references, never payloads
    input_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    effect_intent_ids: tuple[str, ...] = ()

    # Governance
    budget: Budget = Budget()
    spend: BudgetSpend = BudgetSpend()

    run_id: str = field(default_factory=_new_run_id)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # -- authority ---------------------------------------------------

    def grants(self, scopes: frozenset[str]) -> bool:
        """Whether this Run already carries every scope in `scopes`.

        Deliberately has no counterpart that *adds* a scope. Widening
        authority is not a Run operation at all - it requires a new Run
        created through intake, which re-runs policy. See ADR-0012.
        """
        return scopes <= self.approved_scopes

    def permits_zone(self, zone: Zone) -> bool:
        return zone in self.allowed_zones

    def permits_classification(self, classification: Classification) -> bool:
        return classification <= self.classification_ceiling

    def raise_risk_tier(self, tier: RiskTier) -> "Run":
        """Raise the risk tier, never lower it.

        A no-op when the requested tier is already at or below the current
        one, so a validator that spots an external recipient can call this
        unconditionally without having to know what else already raised
        it.
        """
        order = (RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH)
        if order.index(tier) <= order.index(self.risk_tier):
            return self
        return self._touch(risk_tier=tier)

    # -- budget ------------------------------------------------------

    def would_exhaust(
        self, *, steps: int = 1, tokens: int = 0, spend_micros: int = 0, now: float | None = None
    ) -> str | None:
        """Return the name of the budget this charge would break, or None.

        A check rather than an exception so the gateway can decide *before*
        invoking an irreversible capability, rather than discovering the
        budget is gone after the email has left. Returns the dimension
        name so the terminal reason names which budget ran out.
        """
        now = time.time() if now is None else now
        if self.budget.deadline_ts is not None and now > self.budget.deadline_ts:
            return "deadline"
        if self.spend.steps + steps > self.budget.max_steps:
            return "max_steps"
        if self.spend.tokens + tokens > self.budget.max_tokens:
            return "max_tokens"
        if self.spend.spend_micros + spend_micros > self.budget.max_spend_micros:
            return "max_spend_micros"
        return None

    def charge(self, *, steps: int = 1, tokens: int = 0, spend_micros: int = 0) -> "Run":
        """Record consumption. Does not enforce - call `would_exhaust`
        first. Split this way because the enforcement decision belongs to
        whoever knows what to do about it (fail the step, return a partial
        result, transition to BUDGET_EXHAUSTED), not to the accounting.
        """
        return self._touch(
            spend=BudgetSpend(
                steps=self.spend.steps + steps,
                tokens=self.spend.tokens + tokens,
                spend_micros=self.spend.spend_micros + spend_micros,
            )
        )

    # -- progress ----------------------------------------------------

    def with_step(self, result: StepResult) -> "Run":
        return self._touch(step_results=self.step_results + (result,))

    def with_evidence(self, *refs: str) -> "Run":
        new = tuple(r for r in refs if r not in self.evidence_refs)
        return self._touch(evidence_refs=self.evidence_refs + new) if new else self

    def with_effect_intent(self, intent_id: str) -> "Run":
        if intent_id in self.effect_intent_ids:
            return self
        return self._touch(effect_intent_ids=self.effect_intent_ids + (intent_id,))

    def _touch(self, **changes) -> "Run":
        return replace(self, updated_at=time.time(), **changes)
