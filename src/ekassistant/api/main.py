"""API Gateway & Identity.

See docs/design/api-gateway-identity.md. This is the first wired vertical
slice: mock-authenticate a caller and resolve their groups. The /query
endpoint (orchestration -> retrieval -> model layer) lands in a later
commit, per docs/design/orchestration.md.
"""

from functools import lru_cache

import uvicorn
from fastapi import FastAPI, Header

from ekassistant.config.settings import get_settings
from ekassistant.identity.store import IdentityStore

app = FastAPI(title="Enterprise Knowledge Assistant - API Gateway")


@lru_cache
def get_identity_store() -> IdentityStore:
    return IdentityStore.from_yaml(get_settings().identities_path)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/whoami")
def whoami(x_user_id: str | None = Header(default=None)) -> dict:
    """Resolve the mock caller identity to their group set.

    See docs/decisions/0007-mock-identity-and-group-lookup.md: an absent
    header falls back to the configured default user, and an unrecognized
    user resolves to an empty group set rather than an error.
    """
    settings = get_settings()
    user_id = x_user_id or settings.default_user
    groups = get_identity_store().groups_for(user_id)
    return {"user_id": user_id, "groups": groups}


def run() -> None:
    uvicorn.run("ekassistant.api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
