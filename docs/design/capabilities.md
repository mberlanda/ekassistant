# Low-level design: Capabilities and Effects

Reference spine item d (Tool Layer), which
[ADR-0008](../decisions/0008-lightweight-orchestration.md) left as a
documented seam with an empty registry because V1 had nothing to call. This
fills it.

See [ADR-0012](../decisions/0012-capability-registry-and-gateway.md) for
the contract/registry/gateway decision and
[ADR-0013](../decisions/0013-execution-zone-isolation.md) for zones.

## Contracts

`capabilities/contracts.py`. A capability is a **versioned, typed
contract** — never a natural-language description handed to a model,
because the contract is what the gateway enforces against.

```python
ToolContract(
    tool="certified_metric", version=1,
    effect_class=EffectClass.READ_ONLY,
    zone=Zone.INTERNAL_DATA,
    required_scopes=frozenset({"analytics.metrics.read"}),
    max_classification=Classification.RESTRICTED,
    input_model=MetricInput, output_model=MetricOutput,
    limits=Limits(timeout_ms=5_000, max_rows=1_000),
    audit_fields=("principal_id", "tenant_id", "purpose", "metric_id", "result_hash"),
)
```

`version` is part of the registry key. Changing any security-relevant
field — effect class, zone, required scopes, classification ceiling — is a
**new version**, never an edit in place, so an approval recorded against v2
can never be silently satisfied by a differently-scoped v3.

### Effect classes

| Class | Example | Retry | Approval |
|---|---|---|---|
| `PURE` | local formatting, schema validation | safe | no |
| `READ_ONLY` | authorized document, CRM field, certified metric | bounded, if the provider is idempotent | usually no |
| `REVERSIBLE_WRITE` | save a versioned draft | idempotency key plus compensating delete | policy dependent |
| `HIGH_IMPACT_WRITE` | change shared customer data | idempotency plus version precondition | normally explicit |
| `IRREVERSIBLE_EXTERNAL` | send a client email | **never blind-retry**; reconcile first | explicit |

These are consulted as sets (`REQUIRES_EFFECT_INTENT`,
`REQUIRES_APPROVAL`, `NEVER_BLIND_RETRY`) rather than compared by
ordering, because the interesting predicates are different partitions —
"can I retry this?" and "does this need a human?" do not split the same
way.

### Zones

`PURE`, `INTERNAL_DATA`, `WEB_RESEARCH`, `EFFECT`. See
[ADR-0013](../decisions/0013-execution-zone-isolation.md).

`ToolResult.origin_zone` travels with every result and `is_untrusted` keys
off it. **Classification and trust are separate axes**: web content is
`PUBLIC` and untrusted at the same time. Conflating them is how
low-sensitivity text ends up treated as safe.

## Registry

`capabilities/registry.py`. Keyed by `(tool, version)`, append-only within
a process — re-registering the same ref raises rather than replacing,
because a silently swapped implementation behind an unchanged contract is
invisible to an audit trail.

The registry is also the **projection** point for zone isolation:

```python
registry.visible_to(zones=run.allowed_zones, scopes=run.approved_scopes)
```

A model-facing tool list is built from this, never from
`all_contracts()`. A capability outside the projection has no name the
model could utter — a stronger guarantee than refusing the call afterwards,
since a refusal still tells an attacker the tool exists.

Fail-closed by construction: an empty zone set yields an empty list,
because no zone is a member of the empty set. There is no "all zones"
sentinel, deliberately — a bug that leaves a zone set unpopulated must
produce a Run that can do nothing, not one that can do everything.

### Registration-time checks

Refused at `register()`, not at call time, because an over-permissive
capability is otherwise discovered only by the incident it causes:

- a non-`PURE` capability declaring no required scopes (it would be
  reachable by any Run whose zones include its zone);
- any effect in the `PURE` zone, which performs no I/O by definition;
- **any write in the `WEB_RESEARCH` zone** — that would be a direct path
  from attacker-controlled page text to a real-world effect.

## Gateway

`capabilities/gateway.py`. The single place authorization is decided.
Effective permission is an intersection:

```
Run authority ∩ current policy ∩ contract requirements
              ∩ data classification ∩ effect approval
```

Two of those are snapshots (what the Run was granted at intake) and one is
live (what policy says right now). Both are checked, and that is not
redundant: a Run in flight when a group is revoked must lose the
capability, and a policy loosened mid-run must not retroactively widen a
Run created under a narrower one. The snapshot is a ceiling that can only
shrink; live evaluation is what shrinks it.

Order of checks in `invoke()`:

1. Run is not terminal.
2. Budget — checked, then **charged immediately**, so denied attempts cost
   a step.
3. Contract resolves.
4. Run carries the zone, the scopes, and a high enough ceiling.
5. Current policy permits it.
6. Payload validates against `input_model`.
7. Effect intent, if the effect class requires one.
8. Invoke; validate output against `output_model`.

Everything fails closed. No bypass parameter, no privileged caller, no
"internal" flag — a caller needing more authority needs a different Run.

`invoke()` **returns** `InvocationOutcome` on denial rather than raising,
because the Run was charged either way and an exception path makes it far
too easy to drop the charged copy. `unwrap()` is there for call sites that
want exception semantics and have already taken `run`.

### Canonical payloads

The gateway hashes and passes on the **validated** payload, not the raw
input. Pydantic coerces, so the dict a caller supplies and the dict a tool
receives can differ; binding an approval to the raw form would approve
something other than what executes.

Consequently `canonical_payload()` is public and `propose_effect()` exists.
Anything computing a payload hash separately — proposing an intent, showing
a reviewer what they are approving — must normalize identically, or hashes
disagree and every approval fails with a mismatch that looks like
tampering. One shared normalizer removes that class of bug rather than
documenting around it.

## Effects and approval

`capabilities/effects.py`. The rule this exists to make mechanical:

> **An approval is bound to an exact payload, not to an intent.**

Approving "send the client update" and then sending a different body,
recipient or attachment is the most damaging thing available here, and
review discipline cannot prevent it — a reviewer cannot see a payload that
changed after they clicked approve. So `approve()` records the hash the
reviewer actually saw, and `authorized_for()` re-hashes at commit time.
Any edit in between invalidates the approval automatically, and the Run
takes the `AWAITING_APPROVAL → RUNNING` edge back to work.

Intents are written to the ledger **before** execution, not after. Only a
pre-write ledger can distinguish "never attempted" from "attempted, outcome
unknown" — and only the second owes reconciliation.

A `COMMITTED` intent never authorizes again. That is what stops a retried
model call from sending a second email.

The ledger stores a hash plus a caller-supplied, audit-safe summary —
**never the payload**. It is read by operations, incident review and
reconciliation tooling, all places where a full copy of client email bodies
would be a second, less-guarded home for sensitive data.

### Retry and reconciliation

`CapabilityExecutionError` from an `IRREVERSIBLE_EXTERNAL` capability means
the outcome is **unknown**, not that nothing happened. The gateway
deliberately does *not* mark the intent `FAILED` in that case — that would
assert the send did not occur, which nothing at this layer knows. The
intent is left unsettled and reconciliation is owed.

`SendRefused` from the mock provider is the opposite case: a definitive
refusal, so nothing is owed. The distinction is worth preserving in any
real adapter.

Duplicate suppression happens twice, at layers that fail differently: the
ledger's `find_committed()` protects against a retried workflow, and the
provider's own idempotency-key handling protects against two workflows that
each believe they own the same effect.

## Audit

`capabilities/audit.py`. One `InvocationRecord` per invocation **attempt**,
allowed or not.

Distinct from [observability/tracing.py](observability.md): a `RunTrace`
answers "how did this behave" and is read for debugging and quality; an
`InvocationRecord` answers "what was this authorized to do, and what did it
do", is read during incident review, and must be complete even for refused
attempts — a stream of denials is itself the signal.

Records carry identifiers, hashes and policy outcomes. Never payloads. The
audit log is one of the most widely readable artefacts here.

`JsonlAuditSink` uses the same append-only, no-lock approach as
`record_run()` and relies on the same POSIX `O_APPEND` guarantee. Unlike
traces, every field here is an identifier, hash, enum or float, so the
`PIPE_BUF` size caveat is not a practical concern.

## Adding a capability

Three steps, and none of them touch the gateway, the policy engine or any
workflow:

1. Write the class: a `contract` attribute and an `invoke(payload, ctx)`
   method returning a `ToolResult`.
2. Add one line to `CATALOGUE` in `capabilities/bootstrap.py`.
3. Enable it in `config/capabilities.yaml`.

Enabling a capability grants it to nobody. A Run can only invoke what its
purpose's zone list and its groups' scopes already permit — see
[policy.md](policy.md).

A missing or empty `enabled:` list yields an **empty** registry, not the
full catalogue: a truncated config should leave the system able to do
nothing. An unknown name fails at startup rather than being skipped, since
a typo would otherwise silently remove a tool, and "it stopped being able
to do that" is far harder to trace to a config file than a refusal to boot.

## Mocks

`capabilities/mocks/` — one per zone, all fixtures rather than stubs. Each
enforces the invariants its real counterpart will: unknown account raises
rather than returning an empty record (an empty account reads downstream as
a *permission* result); an uncertified metric period refuses rather than
interpolating; send is idempotent and kill-switchable.

`mocks/web.py` ships a **hostile fixture** from Phase 0 — a plausible
injection naming a real capability and a real-looking exfiltration target.
A red-team corpus written after the web workflow exists is written by
someone who already knows what their code does; having it present while the
interfaces are designed is what keeps the constraint real.

## Tradeoffs

- **Ceremony per tool.** A contract plus two pydantic models before
  anything runs, which lands hardest on trivial capabilities. Partly repaid
  by the registration-time checks catching exactly the mistakes the
  ceremony is meant to prevent.
- **A single point of correctness.** Every authorization bug lives in one
  file — excellent for review, terrible if review is shallow. Hence a test
  suite that is overwhelmingly deny-path.
- **In-memory ledger.** Phase 3 owes a durable one. The mock already
  enforces every invariant the real one will, so Phase 3's workflow code is
  not written against rules that will not hold.
