# 0007. Mock authentication with a static group lookup table

Date: 2026-08-07

## Status

Accepted

## Context

The API Gateway & Identity layer (reference spine item b) needs to resolve
"who is asking" into "which groups can they see" for the Access Control
List (ACL) pre-filter (see [ADR-0002](0002-acl-enforcement-at-retrieval.md))
to have anything to filter on. The brief explicitly scopes this down for
V1: "mock authentication and maintain in backend a lookup table of groups /
consider using defaults". Wiring up a real identity provider (SSO, an
enterprise directory) is out of scope for a POC validating the retrieval
and grounding pipeline.

## Decision

Authentication is mocked: the caller supplies a user ID (via a header on
the API Gateway, or a config default for the TUI), and the API Gateway
resolves it to a group set via a **static lookup table** — a YAML file
(`config/identities.yaml`) mapping user ID → list of group IDs, loaded into
an in-memory identity store at startup. No passwords, tokens, or real
authentication protocol are implemented. A `default_user` config value lets
the TUI run with zero setup for a single-user demo.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Real identity provider integration (e.g. an enterprise SSO/directory protocol) | Realistic, production-shaped | Significant setup and credential overhead for a solo POC; the brief explicitly defers this |
| Hardcoded single user/group, no lookup table at all | Zero setup | Can't demonstrate or test the ACL boundary at all — there's nothing to test *against* since every query would see the same access; undermines the one thing the brief says is most important ("strict per-user access control") |
| Static lookup table (chosen) | Lets multiple mock identities with different group memberships exist, so ACL enforcement is actually exercisable and testable end-to-end; trivially easy to set up (one YAML file); the interface (resolve user → groups) is the same shape a real identity provider integration would have, so swapping it later doesn't change any downstream code | Not real security — no credential verification of any kind; must never be mistaken for, or shipped as, an authentication mechanism |

## Tradeoffs of the chosen option

This is explicitly **not authentication** — it is an identity *lookup*
stand-in that lets the rest of the system (ACL pre-filtering, citations,
audit logging) be built and tested against realistic multi-user,
multi-group scenarios without building or integrating a real identity
provider. The API Gateway component boundary is kept clean specifically so
this can be replaced later (see [api-gateway-identity design](../design/api-gateway-identity.md))
without changing anything downstream that only depends on "resolved group
set", not on *how* it was resolved.

## Consequences

- The lookup table ships with a handful of sample users/groups so the ACL
  boundary is demonstrable out of the box (see
  [api-gateway-identity design](../design/api-gateway-identity.md) for the
  concrete sample data).
- Nothing about this mechanism may be exposed outside a local/dev
  environment; this must be called out prominently in the
  [README](../../README.md) so it's never mistaken for a security control.
- Swapping in a real identity provider later only requires replacing the
  identity store's implementation behind its existing interface — the
  rest of the system (retrieval, orchestration) never talks to it
  directly, only to the resolved group set the API Gateway attaches to
  each request.
