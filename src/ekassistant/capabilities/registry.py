"""The capability registry: every tool the system knows about, by version.

See docs/design/capabilities.md#registry and ADR-0012.

The registry is also the *projection* point for zone isolation. A Run does
not get the full tool list filtered later - `visible_to()` builds the list
from the Run's zones and scopes, and that list is all the model is ever
shown. A capability outside it has no name the model could utter, which is
a stronger guarantee than refusing the call afterwards: a refusal still
tells an attacker the tool exists.
"""

from ekassistant.capabilities.contracts import Capability, EffectClass, ToolContract, Zone


class CapabilityNotFound(KeyError):
    pass


class DuplicateCapability(Exception):
    pass


class UnsafeContract(Exception):
    """Raised at registration for a contract that could not be enforced.

    Registration-time rather than call-time, for the same reason
    PolicyConfigError is load-time: a capability that is silently
    over-permissive is only discovered by the incident it causes.
    """


class CapabilityRegistry:
    """Keyed by (tool, version). Registration is append-only within a
    process - re-registering the same ref raises rather than replacing,
    because a silently swapped implementation behind an unchanged contract
    is exactly what an audit trail cannot detect.
    """

    def __init__(self) -> None:
        self._by_ref: dict[str, Capability] = {}
        self._versions: dict[str, list[int]] = {}

    def register(self, capability: Capability) -> None:
        contract = capability.contract
        self._validate(contract)

        if contract.ref in self._by_ref:
            raise DuplicateCapability(f"{contract.ref} is already registered")

        self._by_ref[contract.ref] = capability
        self._versions.setdefault(contract.tool, []).append(contract.version)
        self._versions[contract.tool].sort()

    def get(self, tool: str, version: int | None = None) -> Capability:
        """Resolve a capability. `version=None` means the highest
        registered version.

        Callers that care about authorization stability should pass an
        explicit version: an approval recorded against v2 must not be
        satisfiable by a later v3 with different scopes, and "latest" is
        how that would happen.
        """
        resolved = self._resolve_version(tool, version)
        try:
            return self._by_ref[f"{tool}@{resolved}"]
        except KeyError as exc:
            raise CapabilityNotFound(f"{tool}@{resolved}") from exc

    def contract(self, tool: str, version: int | None = None) -> ToolContract:
        return self.get(tool, version).contract

    def all_contracts(self) -> list[ToolContract]:
        return [cap.contract for cap in self._by_ref.values()]

    def visible_to(self, *, zones: frozenset[Zone], scopes: frozenset[str]) -> list[ToolContract]:
        """The contracts a caller with these zones and scopes may see.

        Fail-closed by construction: an empty zone set yields an empty
        list, since no contract's zone can be a member of the empty set.
        There is no "all zones" sentinel, deliberately - a bug that leaves
        a zone set unpopulated must produce a Run that can do nothing, not
        a Run that can do everything.
        """
        return sorted(
            (
                contract
                for contract in self.all_contracts()
                if contract.zone in zones and contract.required_scopes <= scopes
            ),
            key=lambda c: (c.tool, c.version),
        )

    def _resolve_version(self, tool: str, version: int | None) -> int:
        versions = self._versions.get(tool)
        if not versions:
            raise CapabilityNotFound(tool)
        if version is None:
            return versions[-1]
        if version not in versions:
            raise CapabilityNotFound(f"{tool}@{version}")
        return version

    @staticmethod
    def _validate(contract: ToolContract) -> None:
        if contract.version < 1:
            raise UnsafeContract(f"{contract.tool}: version must be >= 1")

        # A tool that requires no scope is reachable by any Run whose zone
        # set includes its zone. That is fine for PURE tools (local
        # formatting, schema validation - no data, no effect) and is a
        # hole for anything else.
        if contract.effect_class is not EffectClass.PURE and not contract.required_scopes:
            raise UnsafeContract(
                f"{contract.ref}: only PURE capabilities may declare no required scopes; "
                f"{contract.effect_class} needs at least one"
            )

        if contract.zone is Zone.PURE and contract.effect_class is not EffectClass.PURE:
            raise UnsafeContract(
                f"{contract.ref}: the PURE zone performs no I/O, so it cannot host "
                f"{contract.effect_class}"
            )

        # The web zone exists to contain untrusted content. A capability
        # there that can write anything would be a direct path from
        # attacker-controlled page text to a real-world effect.
        if contract.zone is Zone.WEB_RESEARCH and contract.effect_class not in (
            EffectClass.PURE,
            EffectClass.READ_ONLY,
        ):
            raise UnsafeContract(
                f"{contract.ref}: the WEB_RESEARCH zone may only host PURE or READ_ONLY "
                f"capabilities, not {contract.effect_class}"
            )
