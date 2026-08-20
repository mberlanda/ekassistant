"""The output of every policy evaluation.

See docs/design/policy.md. A decision always carries the policy version
that produced it, so a Run's audit trail can answer "would this still be
allowed today?" - which is a different question from "was it allowed then",
and both come up in incident review.
"""

from dataclasses import dataclass

from ekassistant.capabilities.contracts import Classification, EffectClass, Zone


@dataclass(frozen=True)
class PolicyDecision:
    """Allow or deny, always with a reason.

    `reason` is populated on allow as well as deny. An allow-reason looks
    redundant right up until an audit asks *which* rule permitted
    something, at which point reconstructing it from the ruleset as it
    stood months ago is not realistic.
    """

    allowed: bool
    reason: str
    policy_version: str

    @classmethod
    def allow(cls, reason: str, policy_version: str) -> "PolicyDecision":
        return cls(allowed=True, reason=reason, policy_version=policy_version)

    @classmethod
    def deny(cls, reason: str, policy_version: str) -> "PolicyDecision":
        return cls(allowed=False, reason=reason, policy_version=policy_version)


@dataclass(frozen=True)
class PurposeProfile:
    """What a declared purpose permits, resolved at intake.

    This is where a Run's authority comes from. The caller states a
    purpose ("draft a client update"); the profile turns that into the
    concrete zone set, classification ceiling and effect classes the Run
    may ever use. Nothing downstream can widen it - see ADR-0012.
    """

    purpose: str
    allowed_task_types: frozenset[str]
    allowed_zones: frozenset[Zone]
    allowed_effect_classes: frozenset[EffectClass]
    max_classification: Classification
