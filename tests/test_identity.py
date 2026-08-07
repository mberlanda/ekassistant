from pathlib import Path

from ekassistant.identity.store import IdentityStore

IDENTITIES_PATH = Path(__file__).parent.parent / "config" / "identities.yaml"


def test_known_user_resolves_to_configured_groups():
    store = IdentityStore.from_yaml(IDENTITIES_PATH)
    assert store.groups_for("alice") == ["engineering", "all-staff"]


def test_guest_has_no_groups():
    store = IdentityStore.from_yaml(IDENTITIES_PATH)
    assert store.groups_for("guest") == []


def test_unknown_user_fails_closed_to_no_groups():
    store = IdentityStore.from_yaml(IDENTITIES_PATH)
    assert store.groups_for("someone-not-in-the-table") == []
