"""Intake: the one place a Run's authority is created.

See docs/design/runs.md#intake and ADR-0012.

Everything downstream can only narrow what happens here. A workflow step
cannot add a scope; the gateway cannot grant a zone; a model cannot name a
capability outside the set this function fixed. That is the whole content
of "authority is carried, never generated" - and it holds only because
there is exactly one constructor, and it derives authority from policy
rather than accepting it as an argument.

Note the signature: `groups` and `purpose` go in, scopes and zones come
out. A caller cannot pass `approved_scopes` even if it wants to.
"""

from dataclasses import dataclass

from ekassistant.policy.decision import PolicyDecision
from ekassistant.policy.engine import PolicyEngine
from ekassistant.runs.aggregate import Budget, RiskTier, Run


@dataclass(frozen=True)
class IntakeRequest:
    tenant_id: str
    principal_id: str
    groups: list[str]
    purpose: str
    task_type: str
    input_refs: tuple[str, ...] = ()
    risk_tier: RiskTier = RiskTier.LOW
    budget: Budget = Budget()


class IntakeRejected(Exception):
    """Intake refused to create a Run. Carries the PolicyDecision so the
    caller can surface the reason and the policy version that produced it,
    rather than a generic 403 that nobody can act on.
    """

    def __init__(self, decision: PolicyDecision):
        super().__init__(decision.reason)
        self.decision = decision


def create_run(request: IntakeRequest, policy: PolicyEngine) -> Run:
    """Resolve purpose + groups into a Run carrying fixed authority.

    Raises IntakeRejected for an unknown purpose or a task type the
    purpose does not cover. Both are fail-closed: an unrecognised purpose
    yields no Run at all, never a Run with a permissive default.
    """
    profile = policy.profile_for(request.purpose)
    if profile is None:
        raise IntakeRejected(
            PolicyDecision.deny(
                f"unknown purpose {request.purpose!r}", policy.policy_version
            )
        )

    if request.task_type not in profile.allowed_task_types:
        raise IntakeRejected(
            PolicyDecision.deny(
                f"task type {request.task_type!r} not permitted for "
                f"purpose {request.purpose!r}",
                policy.policy_version,
            )
        )

    # The intersection, not the union. A principal in a group granting
    # `email.send` gets that scope on a Run only if the declared purpose
    # also permits IRREVERSIBLE_EXTERNAL effects - so the same person
    # doing market research carries a strictly smaller Run than the same
    # person drafting a client email, without having to hold two accounts.
    granted = policy.scopes_for_groups(request.groups)

    return Run(
        tenant_id=request.tenant_id,
        principal_id=request.principal_id,
        purpose=request.purpose,
        task_type=request.task_type,
        approved_scopes=granted,
        allowed_zones=profile.allowed_zones,
        classification_ceiling=profile.max_classification,
        risk_tier=request.risk_tier,
        policy_version=policy.policy_version,
        input_refs=request.input_refs,
        budget=request.budget,
    )
