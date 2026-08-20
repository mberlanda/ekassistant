"""Static group lookup table.

See docs/decisions/0007-mock-identity-and-group-lookup.md: this resolves
"who is asking" to "which groups can they see" as a stand-in for a real
identity provider. An unknown user resolves to an empty group set
(fail closed - sees nothing), never to a default/admin group.
"""

from pathlib import Path

import yaml

DEFAULT_TENANT = "default"


class IdentityStore:
    def __init__(
        self,
        groups_by_user: dict[str, list[str]],
        tenant_by_user: dict[str, str] | None = None,
    ):
        self._groups_by_user = groups_by_user
        self._tenant_by_user = tenant_by_user or {}

    @classmethod
    def from_yaml(cls, path: Path) -> "IdentityStore":
        raw = yaml.safe_load(path.read_text()) or {}
        groups_by_user = {user: entry.get("groups", []) for user, entry in raw.items()}
        tenant_by_user = {
            user: entry["tenant"] for user, entry in raw.items() if entry.get("tenant")
        }
        return cls(groups_by_user, tenant_by_user)

    def groups_for(self, user_id: str) -> list[str]:
        return self._groups_by_user.get(user_id, [])

    def tenant_for(self, user_id: str) -> str:
        """Which tenant this caller belongs to.

        Falls back to DEFAULT_TENANT rather than raising, so an identity
        file predating the `tenant:` key still resolves. That fallback is
        safe only because tenancy here is uniform; the moment a second
        real tenant exists, an unlabelled user must become an error, not a
        default - a caller silently landing in the wrong tenant is the
        worst possible failure of this lookup.
        """
        return self._tenant_by_user.get(user_id, DEFAULT_TENANT)

    def all_user_ids(self) -> list[str]:
        """Every user_id known to this lookup table - used by the eval
        harness (docs/design/observability.md) to build ACL test cases
        across every mock user, not a set hardcoded separately from
        config/identities.yaml.
        """
        return list(self._groups_by_user)
