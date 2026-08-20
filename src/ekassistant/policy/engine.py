"""Policy Decision Point: evaluated at intake, and again at every call.

See docs/design/policy.md and ADR-0012.

Two evaluations, on purpose:

* **At intake**, `profile_for()` + `scopes_for_groups()` turn "who is
  asking, for what purpose" into the fixed authority a Run carries -
  its zone set, scope set and classification ceiling.
* **At every capability call**, `evaluate()` re-checks the same rules
  against the *current* policy version.

The second looks redundant against the first and is not. A Run can be
long-lived; a group can be revoked or a purpose narrowed while it is in
flight. The Run's snapshot is a ceiling that can only shrink, and the
live evaluation is what shrinks it - so authorization is the intersection
of what the caller was granted and what policy still permits, never
whichever of the two is more generous.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    ToolContract,
    Zone,
)
from ekassistant.policy.decision import PolicyDecision, PurposeProfile


@dataclass(frozen=True)
class PolicyRequest:
    """One authorization question: may this principal, acting for this
    purpose, invoke this exact contract?

    Carries the whole ToolContract rather than a tool name, so the engine
    reasons about the declared effect class and zone of the version being
    invoked - not whatever the name currently maps to.
    """

    tenant_id: str
    principal_id: str
    purpose: str
    task_type: str
    granted_scopes: frozenset[str]
    contract: ToolContract


class PolicyEngine(Protocol):
    policy_version: str

    def scopes_for_groups(self, groups: list[str]) -> frozenset[str]: ...

    def profile_for(self, purpose: str) -> PurposeProfile | None: ...

    def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...


class PolicyConfigError(Exception):
    """Raised at load time for an unparseable policy file.

    Loud at startup rather than fail-closed at runtime: a typo in a zone
    or classification name would otherwise quietly narrow a purpose's
    authority, and a capability that mysteriously stops being permitted is
    far harder to diagnose than a service that refuses to boot.
    """


class ScopePolicyEngine:
    """Config-driven policy engine over config/policies.yaml.

    Mirrors the mock-identity tradeoff in ADR-0007: a static file standing
    in for a real policy service, honest about being a stand-in. What is
    *not* mocked is the shape of the decision - purposes, zones, effect
    classes and classification ceilings are the real vocabulary, so
    swapping the backing store later does not change any caller.
    """

    def __init__(
        self,
        policy_version: str,
        scopes_by_group: dict[str, frozenset[str]],
        profiles: dict[str, PurposeProfile],
    ):
        self.policy_version = policy_version
        self._scopes_by_group = scopes_by_group
        self._profiles = profiles

    @classmethod
    def from_yaml(cls, path: Path) -> "ScopePolicyEngine":
        raw = yaml.safe_load(path.read_text()) or {}
        version = str(raw.get("policy_version", "0"))

        scopes_by_group = {
            group: frozenset(scopes or [])
            for group, scopes in (raw.get("scopes_by_group") or {}).items()
        }

        profiles: dict[str, PurposeProfile] = {}
        for purpose, entry in (raw.get("purposes") or {}).items():
            entry = entry or {}
            profiles[purpose] = PurposeProfile(
                purpose=purpose,
                allowed_task_types=frozenset(entry.get("allowed_task_types") or []),
                allowed_zones=frozenset(
                    _parse_enum(Zone, name, "zone", purpose)
                    for name in (entry.get("allowed_zones") or [])
                ),
                allowed_effect_classes=frozenset(
                    _parse_enum(EffectClass, name, "effect class", purpose)
                    for name in (entry.get("allowed_effect_classes") or [])
                ),
                max_classification=_parse_classification(
                    entry.get("max_classification", "PUBLIC"), purpose
                ),
            )

        return cls(version, scopes_by_group, profiles)

    def scopes_for_groups(self, groups: list[str]) -> frozenset[str]:
        """Union of the scopes each group grants.

        An unknown group contributes nothing rather than raising - it
        mirrors IdentityStore.groups_for()'s fail-closed behaviour, and a
        directory that lists a group this policy file has not heard of is
        a normal transitional state, not an error.
        """
        granted: set[str] = set()
        for group in groups:
            granted |= self._scopes_by_group.get(group, frozenset())
        return frozenset(granted)

    def profile_for(self, purpose: str) -> PurposeProfile | None:
        return self._profiles.get(purpose)

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Deny unless every check passes. Order matters only for the
        quality of the reason string - the first failure is the one
        reported, so the checks run cheapest and most-explanatory first.
        """
        def deny(reason: str) -> PolicyDecision:
            return PolicyDecision.deny(reason, self.policy_version)

        profile = self._profiles.get(request.purpose)
        if profile is None:
            return deny(f"unknown purpose {request.purpose!r}")

        if request.task_type not in profile.allowed_task_types:
            return deny(
                f"task type {request.task_type!r} not permitted for purpose {request.purpose!r}"
            )

        contract = request.contract

        if contract.zone not in profile.allowed_zones:
            return deny(
                f"zone {contract.zone} not permitted for purpose {request.purpose!r}"
            )

        if contract.effect_class not in profile.allowed_effect_classes:
            return deny(
                f"effect class {contract.effect_class} not permitted for "
                f"purpose {request.purpose!r}"
            )

        if contract.max_classification > profile.max_classification:
            return deny(
                f"{contract.ref} handles {contract.max_classification.name} data, above "
                f"purpose {request.purpose!r} ceiling of {profile.max_classification.name}"
            )

        missing = contract.required_scopes - request.granted_scopes
        if missing:
            return deny(f"missing scopes {sorted(missing)} for {contract.ref}")

        return PolicyDecision.allow(
            f"{contract.ref} permitted for purpose {request.purpose!r}", self.policy_version
        )


def _parse_enum(enum_cls, name: str, label: str, purpose: str):
    try:
        return enum_cls(name)
    except ValueError as exc:
        raise PolicyConfigError(
            f"unknown {label} {name!r} under purpose {purpose!r}; "
            f"expected one of {[m.value for m in enum_cls]}"
        ) from exc


def _parse_classification(name: str, purpose: str) -> Classification:
    try:
        return Classification[str(name).upper()]
    except KeyError as exc:
        raise PolicyConfigError(
            f"unknown classification {name!r} under purpose {purpose!r}; "
            f"expected one of {[m.name for m in Classification]}"
        ) from exc
