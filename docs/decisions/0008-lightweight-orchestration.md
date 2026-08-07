# 0008. Hand-write orchestration instead of adopting an agent framework

Date: 2026-08-07

## Status

Accepted

## Context

The reference spine's Orchestration layer (item c) is described as an
"agent runtime: planner/router, tool-caller, state manager, approval
gates". Frameworks like LangChain or LlamaIndex offer pre-built versions of
these pieces. But the brief also explicitly **defers** agentic multi-hop
retrieval and cross-source reasoning to a later phase — V1's actual
orchestration job is much narrower: rewrite/route one query, call the
retriever once, assemble context, call the LLM once, return a cited answer
or an abstain response (pipeline stages `c` through `e`).

## Decision

Write the V1 orchestration layer as **plain Python** — a small, explicit
pipeline (query rewrite → retrieve → assemble context → generate →
validate citations) — rather than adopting a general-purpose agent
framework.

## Alternatives considered

| Option | Pros | Cons |
|---|---|---|
| LangChain / LlamaIndex (or similar) | Batteries-included abstractions for retrievers, chains/pipelines, agents; large ecosystem; would pay off *if* multi-hop agentic behavior were in scope now | Brings abstractions (agents, tool-calling loops, memory managers) sized for a scope this project explicitly defers; adds a large, fast-moving dependency; makes the ACL pre-filter and cite-or-abstain contract (the two things this project actually needs to get exactly right) implicit inside framework internals instead of explicit, auditable code |
| Hand-written orchestration (chosen) | Every step of the pipeline — especially the ACL pre-filter and the citation/abstain check — is explicit, readable, and directly testable; no framework upgrade churn; no functionality paid for but unused | More code to write for pieces a framework would give for free *if and when* the scope grows (multi-step planning, tool-call loops, cross-source agentic reasoning) |

## Tradeoffs of the chosen option

This is a deliberate scope match, not a rejection of agent frameworks in
general: the moment multi-hop agentic retrieval or cross-source reasoning
comes into scope (both currently deferred, see [the brief](../context/brief.md)),
a framework's tool-calling loop and state manager start earning their
complexity, and this decision should be revisited. Adopting one now, before
that need exists, would mean debugging framework abstractions for
behavior V1 doesn't use, on top of a security-critical pipeline
(ACL enforcement) that benefits most from being obvious and inspectable.

## Consequences

- The orchestration module (see [orchestration design](../design/orchestration.md))
  exposes explicit function boundaries for each pipeline stage, making
  citation/abstain logic and ACL pre-filter placement directly unit
  testable without mocking framework internals.
- "Approval gates" and "tool layer" (spine items c/d) stay minimal in V1
  since write actions are deferred (see [the brief](../context/brief.md))
  — there's no destructive tool call to gate yet. Their interfaces are
  still sketched in [orchestration design](../design/orchestration.md) so
  V2 has a seam to extend into.
- If/when agentic multi-hop retrieval is picked up, this ADR should be
  revisited alongside a fresh evaluation of frameworks available at that
  time, rather than assuming this decision still holds.
