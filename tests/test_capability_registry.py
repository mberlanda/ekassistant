"""CapabilityRegistry: versioning, the visibility projection, and the
registration-time safety checks."""

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    InvocationContext,
    ToolContract,
    ToolResult,
    Zone,
)
from ekassistant.capabilities.mocks._shared import mock_result
from ekassistant.capabilities.registry import (
    CapabilityNotFound,
    CapabilityRegistry,
    DuplicateCapability,
    UnsafeContract,
)


class Payload(BaseModel):
    pass


def build_contract(
    tool: str = "sample",
    version: int = 1,
    effect_class: EffectClass = EffectClass.READ_ONLY,
    zone: Zone = Zone.INTERNAL_DATA,
    scopes: frozenset[str] = frozenset({"sample.read"}),
    classification: Classification = Classification.INTERNAL,
) -> ToolContract:
    return ToolContract(
        tool=tool,
        version=version,
        effect_class=effect_class,
        zone=zone,
        required_scopes=scopes,
        max_classification=classification,
        input_model=Payload,
        output_model=Payload,
    )


class Sample:
    def __init__(self, contract: ToolContract):
        self.contract = contract

    def invoke(self, payload: Mapping[str, Any], ctx: InvocationContext) -> ToolResult:
        return mock_result(self.contract, {}, source_ref="test://sample")


def test_ref_is_name_at_version():
    assert build_contract("crm", 3).ref == "crm@3"


def test_get_resolves_the_highest_version_by_default():
    registry = CapabilityRegistry()
    registry.register(Sample(build_contract("sample", 1)))
    registry.register(Sample(build_contract("sample", 3)))
    registry.register(Sample(build_contract("sample", 2)))

    assert registry.get("sample").contract.version == 3
    assert registry.get("sample", 1).contract.version == 1


def test_unknown_tool_or_version_raises():
    registry = CapabilityRegistry()
    registry.register(Sample(build_contract("sample", 1)))

    with pytest.raises(CapabilityNotFound):
        registry.get("other")
    with pytest.raises(CapabilityNotFound):
        registry.get("sample", 7)


def test_registering_the_same_ref_twice_raises():
    # Replacement is refused rather than allowed: a swapped implementation
    # behind an unchanged contract is invisible to an audit trail.
    registry = CapabilityRegistry()
    registry.register(Sample(build_contract()))
    with pytest.raises(DuplicateCapability):
        registry.register(Sample(build_contract()))


def test_visible_to_filters_on_both_zone_and_scope():
    registry = CapabilityRegistry()
    registry.register(Sample(build_contract("internal", scopes=frozenset({"a"}))))
    registry.register(
        Sample(build_contract("web", zone=Zone.WEB_RESEARCH, scopes=frozenset({"b"})))
    )

    visible = registry.visible_to(zones=frozenset({Zone.INTERNAL_DATA}), scopes=frozenset({"a"}))
    assert [c.tool for c in visible] == ["internal"]

    # Right zone, missing scope.
    assert registry.visible_to(zones=frozenset({Zone.WEB_RESEARCH}), scopes=frozenset({"a"})) == []
    # Right scope, wrong zone.
    assert registry.visible_to(zones=frozenset({Zone.EFFECT}), scopes=frozenset({"a"})) == []


def test_no_zones_means_nothing_is_visible():
    # Fail-closed by construction: there is no "all zones" sentinel, so an
    # unpopulated zone set yields a Run that can do nothing.
    registry = CapabilityRegistry()
    registry.register(Sample(build_contract()))
    assert registry.visible_to(zones=frozenset(), scopes=frozenset({"sample.read"})) == []


def test_visible_to_is_ordered_deterministically():
    registry = CapabilityRegistry()
    for tool in ("zulu", "alpha", "mike"):
        registry.register(Sample(build_contract(tool, scopes=frozenset({"s"}))))

    visible = registry.visible_to(
        zones=frozenset({Zone.INTERNAL_DATA}), scopes=frozenset({"s"})
    )
    assert [c.tool for c in visible] == ["alpha", "mike", "zulu"]


def test_non_pure_capability_must_require_a_scope():
    with pytest.raises(UnsafeContract) as exc:
        CapabilityRegistry().register(Sample(build_contract(scopes=frozenset())))
    assert "PURE" in str(exc.value)


def test_pure_capability_may_require_no_scope():
    registry = CapabilityRegistry()
    registry.register(
        Sample(
            build_contract(
                "format", effect_class=EffectClass.PURE, zone=Zone.PURE, scopes=frozenset()
            )
        )
    )
    assert registry.get("format")


def test_pure_zone_cannot_host_an_effect():
    with pytest.raises(UnsafeContract):
        CapabilityRegistry().register(
            Sample(build_contract(zone=Zone.PURE, effect_class=EffectClass.REVERSIBLE_WRITE))
        )


def test_web_zone_cannot_host_a_write():
    # A write reachable from the zone that reads untrusted pages would be
    # a direct path from attacker-controlled text to a real-world effect.
    for effect in (
        EffectClass.REVERSIBLE_WRITE,
        EffectClass.HIGH_IMPACT_WRITE,
        EffectClass.IRREVERSIBLE_EXTERNAL,
    ):
        with pytest.raises(UnsafeContract):
            CapabilityRegistry().register(
                Sample(build_contract(zone=Zone.WEB_RESEARCH, effect_class=effect))
            )


def test_version_must_be_positive():
    with pytest.raises(UnsafeContract):
        CapabilityRegistry().register(Sample(build_contract(version=0)))
