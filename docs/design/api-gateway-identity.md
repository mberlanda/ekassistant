# Low-level design: API Gateway & Identity

Reference spine item b. The single entry point every client (the
[TUI](client-tui.md) in V1) calls through; resolves "who is asking" into
"which groups can they see" before anything reaches
[orchestration](orchestration.md).

## Responsibilities

1. **HTTP API**: exposes one primary endpoint (`POST /query`: question in,
   answer + citations or abstain out) plus a health check. Built with
   FastAPI for its OpenAPI schema generation — the interactive
   `/docs` page it generates for free doubles as hypertext-navigable API
   documentation, in the same spirit as the rest of this project's docs.
2. **Mock authentication**: reads a user ID from a request header
   (`X-User-Id`), falling back to a configured `default_user` if absent —
   see [ADR-0007](../decisions/0007-mock-identity-and-group-lookup.md).
   No password or token verification is performed.
3. **Identity resolution**: looks up the user ID in the static group
   lookup table (`config/identities.yaml`) and attaches the resolved group
   set to the request context passed into orchestration. An unknown user
   ID resolves to an empty group set (fail closed — sees nothing), never
   to a default/admin group.
4. **Request/response shape**: defines the stable contract clients code
   against — `POST /query` with `{question}` in the body and identity via
   the `X-User-Id` header (same header `/whoami` uses, per item 2 above —
   there is no separate `user_id` field in the body), returning
   `{answer, citations[], abstained}` — so the [TUI](client-tui.md) (or
   any future client) never needs to know about retrieval, ranking, or
   model internals.

## Sample identity data (ships with the repo for demos/tests)

| User ID | Groups |
|---|---|
| `alice` | `engineering`, `all-staff` |
| `bob` | `support`, `all-staff` |
| `carol` | `finance`, `all-staff` |
| `guest` | (none) |

Chosen specifically so at least one user (`guest`) has no elevated access,
making the ACL boundary demonstrable out of the box: the same question
asked as `alice` vs. `guest` should visibly differ in retrieved/cited
content whenever the answer depends on a group-restricted document.

## Tradeoffs

- **A real HTTP boundary even though V1 has one client**: running the API
  Gateway as an actual local HTTP service (rather than the TUI calling
  orchestration in-process) costs a small amount of setup (run two
  processes, or one process with two entry points) in exchange for
  keeping the client/server boundary real from day one — a web UI or a
  second client later is an additive change, not a refactor that has to
  first extract a boundary that was never there.
- **Header-based mock user ID, no session/cookie mechanism**: simplest
  possible thing that lets different requests present different identities
  (useful for demoing/testing the ACL boundary across users), at the cost
  of being trivially spoofable — acceptable only because this is
  explicitly not a security boundary, see
  [ADR-0007](../decisions/0007-mock-identity-and-group-lookup.md).
- **Unknown user → empty group set, not an error**: returning "no access"
  rather than rejecting the request keeps the failure mode consistent with
  the rest of the system's fail-closed posture (an unrecognized identity
  behaves like a valid identity with no permissions, not like a system
  error) — this trades a slightly less obvious error message for one
  fewer special case in the ACL enforcement path.
