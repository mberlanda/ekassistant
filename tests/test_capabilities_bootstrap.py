"""Building a registry from the catalogue and from shipped config."""

from pathlib import Path

import pytest

from ekassistant.capabilities.bootstrap import (
    CATALOGUE,
    UnknownCapability,
    build_registry,
    registry_from_yaml,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_catalogue_names_match_their_contracts():
    # The config file addresses capabilities by catalogue key, so a key
    # that disagrees with the contract's own tool name would make
    # config/capabilities.yaml quietly misleading.
    for name, factory in CATALOGUE.items():
        assert factory().contract.tool == name


def test_build_registry_defaults_to_the_whole_catalogue():
    assert {c.tool for c in build_registry().all_contracts()} == set(CATALOGUE)


def test_build_registry_can_be_narrowed():
    registry = build_registry(["web_search"])
    assert [c.tool for c in registry.all_contracts()] == ["web_search"]


def test_unknown_name_fails_loudly():
    with pytest.raises(UnknownCapability) as exc:
        build_registry(["definitely_not_a_tool"])
    assert "definitely_not_a_tool" in str(exc.value)


def test_factories_produce_independent_instances():
    # Stateful capabilities (the outbox, the draft store) must not leak
    # between registries, or one test's send appears in another's outbox.
    a = build_registry(["send_email"]).get("send_email")
    b = build_registry(["send_email"]).get("send_email")

    a.outbox.append({"provider_message_id": "msg_x"})
    assert b.outbox == []


def test_shipped_config_loads():
    registry = registry_from_yaml(REPO_ROOT / "config" / "capabilities.yaml")
    tools = {c.tool for c in registry.all_contracts()}
    assert tools == set(CATALOGUE)


def test_empty_config_yields_an_empty_registry(tmp_path):
    # Fail-closed: a truncated config must leave the system able to do
    # nothing, never able to do everything.
    path = tmp_path / "capabilities.yaml"
    path.write_text("enabled:\n")
    assert registry_from_yaml(path).all_contracts() == []


def test_research_only_deployment_is_a_config_change(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text("enabled:\n  - web_search\n")
    registry = registry_from_yaml(path)

    assert [c.tool for c in registry.all_contracts()] == ["web_search"]
