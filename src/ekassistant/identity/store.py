"""Static group lookup table.

See docs/decisions/0007-mock-identity-and-group-lookup.md: this resolves
"who is asking" to "which groups can they see" as a stand-in for a real
identity provider. An unknown user resolves to an empty group set
(fail closed - sees nothing), never to a default/admin group.
"""

from pathlib import Path

import yaml


class IdentityStore:
    def __init__(self, groups_by_user: dict[str, list[str]]):
        self._groups_by_user = groups_by_user

    @classmethod
    def from_yaml(cls, path: Path) -> "IdentityStore":
        raw = yaml.safe_load(path.read_text()) or {}
        groups_by_user = {user: entry.get("groups", []) for user, entry in raw.items()}
        return cls(groups_by_user)

    def groups_for(self, user_id: str) -> list[str]:
        return self._groups_by_user.get(user_id, [])
