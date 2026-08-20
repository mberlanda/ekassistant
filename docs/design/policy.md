# Low-level design: Identity, Delegation and Policy

Reference spine item i (Governance), specifically the "policy engine" piece
that [governance.md](governance.md) listed as deferred. Sits between
[identity](api-gateway-identity.md) — who is asking — and
[capabilities](capabilities.md) — what may be invoked.

See [ADR-0012](../decisions/0012-capability-registry-and-gateway.md).

## Why this is not part of `identity/`

`identity/store.py` answers "who is this caller and which groups are they
in". This module answers "what may a caller in those groups do, for this
declared purpose".

Keeping them apart is not tidiness. Group membership is directory data,
changed by whoever administers the directory; the mapping from a group to a
capability scope is a policy decision with a different owner and a
different review path. Conflating them means a routine directory edit
silently becomes a policy change, which is the kind of thing nobody notices
until an audit.

## Purpose is the unit of authority

A caller does not request scopes. They declare a **purpose**, and the
purpose profile determines what a Run may ever do:

```yaml
purposes:
  market_research:
    allowed_task_types: [market_research]
    allowed_zones: [PURE, WEB_RESEARCH]
    allowed_effect_classes: [PURE, READ_ONLY]
    max_classification: PUBLIC
```

The consequence worth stating plainly: **the same person gets different
authority for different purposes.** A user who holds `email.send` through
group membership and declares a research purpose gets a Run that still
carries the scope but not the `EFFECT` zone — so the scope is unusable.
One person, one directory entry, different blast radius per task. Without
this, the only way to achieve the separation would be two accounts.

## What the shipped config enforces

`config/policies.yaml`, and the thing to read it for is what is **absent**:

| Purpose | Zones | Ceiling | Cannot |
|---|---|---|---|
| `knowledge_qa` | PURE, INTERNAL_DATA | CONFIDENTIAL | reach the web; write anything |
| `client_communication` | PURE, INTERNAL_DATA, EFFECT | CONFIDENTIAL | **hold untrusted web content** |
| `market_research` | PURE, WEB_RESEARCH | PUBLIC | **see internal data; send anything** |
| `internal_analytics` | PURE, INTERNAL_DATA | RESTRICTED | reach the web; write anything |

No purpose combines private data, untrusted content and an outbound
channel. That is [ADR-0013](../decisions/0013-execution-zone-isolation.md),
and this file is where it is actually enforced rather than merely intended.

`test_policy_engine.py` asserts it against the **shipped** file, not a
synthetic fixture. A test over a purpose-built policy would pass
cheerfully while the deployed config had a hole in it.

## Two evaluations, on purpose

```
intake       profile_for(purpose) + scopes_for_groups(groups)  →  Run authority
                                                                        │
every call   evaluate(PolicyRequest)  ←  current policy version         │
                                                                        ▼
                                        gateway intersects both, takes the smaller
```

The second looks redundant against the first and is not. A Run can be
long-lived; a group can be revoked or a purpose narrowed while it is in
flight. The Run's snapshot is a ceiling that only shrinks, and live
evaluation is what shrinks it — so authorization is the intersection of
what the caller was granted and what policy still permits, never whichever
is more generous.

This is also why `policy_version` is stamped on every Run and every audit
record. "Was this permitted then?" and "would it still be permitted now?"
are different questions, and incident review asks both.

## Decisions carry reasons on allow, too

`PolicyDecision.allow()` takes a reason. That looks redundant right up
until an audit asks *which* rule permitted something, at which point
reconstructing it from the ruleset as it stood months ago is not realistic.

## Failure behaviour

| Situation | Behaviour | Why |
|---|---|---|
| Unknown purpose | deny | Fail closed; there is no default profile |
| Unknown group in `scopes_for_groups` | contributes nothing, no error | Mirrors `IdentityStore.groups_for()`; a directory listing a group this file has not heard of is a normal transitional state |
| No groups | empty scope set | Same fail-closed shape as `guest` in V1 |
| Unparseable zone/classification name in config | **raises at load time** | A typo would otherwise quietly narrow a purpose, and a capability that mysteriously stops being permitted is much harder to diagnose than a service that refuses to boot |
| Empty config file | engine that permits nothing | A truncated config must leave the system unable to act, never able to act freely |

The asymmetry between rows 2 and 4 is deliberate: an unknown *group* is a
data condition that will resolve itself, an unknown *zone name* is a
mistake in a file a human wrote.

## Mock, and honest about it

This is a static YAML file standing in for what would really be a governed,
versioned policy service — the same tradeoff
[ADR-0007](../decisions/0007-mock-identity-and-group-lookup.md) makes for
identity, and it must never be treated as a security boundary in itself.

What is *not* mocked is the vocabulary. Purposes, zones, effect classes and
classification ceilings are the real decision inputs, so replacing the
backing store changes no caller.

## Tradeoffs

- **Purpose-based rather than role-based.** More expressive and it is what
  makes per-task blast radius possible, but it puts a burden on intake to
  declare the right purpose — and a caller who always declares the widest
  purpose available to them defeats it. Mitigation is that the widest
  purpose here still cannot combine all three risk surfaces.
- **Static file, no delegation chain.** There is no on-behalf-of token, no
  consent record and no expiry. Real delegation is Phase 2+ work; the
  `delegation_ref` seam is named in the Run design and not yet populated.
- **Scopes are flat strings.** No hierarchy, no wildcards. Simple to reason
  about and to test; will get repetitive if the capability set grows large.
- **Groups do double duty.** They drive both retrieval ACLs
  ([ADR-0002](../decisions/0002-acl-enforcement-at-retrieval.md)) and
  capability scopes, resolved by different subsystems. Convenient, and a
  place where a future change could surprise someone who only knew about
  one of the two uses.
