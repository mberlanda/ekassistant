# 0012. Put every tool behind a typed contract and one enforcement gateway

Date: 2026-08-10

## Status

Accepted

## Context

[ADR-0008](0008-lightweight-orchestration.md) left the Tool Layer as a
documented seam with an empty registry, on the grounds that V1 had no tool
to call and no write action to gate. [The V2 brief](../context/brief-v2.md)
supplies both: reading CRM records, searching the web, saving drafts and
sending email.

The default way to add tools to an LLM system is to hand the model a list
of function descriptions and let it call them. That works, and it puts the
authorization decision in the worst possible place — inside whatever the
model decided to emit. A tool described only in natural language has no
declared effect class, no scope requirement and no data ceiling, so
"should this caller be allowed to do this right now" becomes a question
each tool answers for itself, differently, if at all.

## Decision

Every capability is a **versioned, typed contract**; contracts live in a
**registry**; and all invocation goes through a single **gateway** that is
the only place authorization is decided.

A `ToolContract` declares `effect_class`, `zone`, `required_scopes`,
`max_classification`, input and output schemas, limits and audit fields.
`CapabilityGateway.invoke()` computes effective permission as an
intersection —

```
Run authority ∩ current policy ∩ contract requirements
              ∩ data classification ∩ effect approval
```

— and denies unless every term passes. There is no bypass parameter, no
privileged caller and no "internal" flag.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| Natural-language tool descriptions, model-driven calls | Standard, minimal ceremony, works with every provider's tool-calling API | Authorization ends up inside model output; no declared effect class means no basis for retry or approval rules; each tool re-implements its own permission check, or skips it |
| Per-tool authorization (each capability checks its own caller) | Simple to add one tool; no central component | The checks drift immediately — five tools mean five subtly different interpretations of the same policy, and the one that forgot is invisible until it matters. Nothing can answer "what is this Run allowed to do" without reading every tool |
| Typed contracts, central registry and gateway (chosen) | One enforcement point to audit and test; effect class gives retry and approval rules a basis; the registry can *project* a per-Run tool list rather than filtering after the fact | More ceremony per tool: a contract, two pydantic models and a catalogue entry before anything runs. Central gateway is a single point of failure for correctness — a bug there is a bug everywhere |

## Tradeoffs of the chosen option

The gateway being a single point of correctness cuts both ways. Every
authorization bug lives in one file, which is excellent for review and
terrible if the review is shallow — hence the test suite for it is
overwhelmingly deny-path tests, and the allow cases are the short section.

The ceremony cost is real and lands hardest on trivial tools. A pure local
formatting helper still needs a contract. The registry's registration-time
checks partly compensate by catching the mistakes that ceremony is supposed
to prevent (a non-`PURE` capability declaring no required scopes; a write
capability placed in the web zone), so the boilerplate is at least load
bearing.

One design detail worth defending: the gateway hashes and passes on the
**validated** payload, not the raw input. Pydantic coerces, so the dict a
caller supplies and the dict a tool receives can differ. Binding an
approval to the raw form would approve something other than what executes.
The consequence is that anything computing a payload hash separately must
normalize identically, which is why `canonical_payload()` is public and why
`propose_effect()` exists rather than callers reaching for the ledger.

## Consequences

- **Authority is carried, never generated.** A Run's scopes and zones are
  fixed at intake (`runs/intake.py`), which is the only constructor that
  derives them from policy. `IntakeRequest` deliberately has no
  `approved_scopes` field, and a test asserts it never grows one.
- The registry **projects** rather than filters: `visible_to()` builds the
  tool list from a Run's zones and scopes, and that list is all a model is
  ever shown. A capability outside it has no name the model could utter —
  stronger than refusing afterwards, since a refusal still reveals that the
  tool exists.
- Effect classes drive retry and approval mechanically rather than by
  convention: `REQUIRES_EFFECT_INTENT`, `REQUIRES_APPROVAL` and
  `NEVER_BLIND_RETRY` are sets in `contracts.py`, and the gateway consults
  them instead of special-casing tool names.
- Denied attempts consume budget. A model looping on a forbidden tool runs
  out rather than looping for free.
- Adding a real capability is: write the class, add one catalogue line in
  `bootstrap.py`, enable it in `config/capabilities.yaml`. No gateway,
  policy or workflow change.
- The audit trail records identifiers, hashes and policy outcomes — never
  payloads. See [the capabilities design](../design/capabilities.md#audit).
