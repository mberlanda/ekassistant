# 0014. Use deterministic workflow templates with bounded model decisions

Date: 2026-08-10

## Status

Accepted. Extends [ADR-0008](0008-lightweight-orchestration.md) rather than
superseding it — that ADR predicted this decision would need revisiting
"if/when agentic multi-hop retrieval is picked up", and this is that
revisit.

## Context

ADR-0008 chose hand-written orchestration over an agent framework, on the
grounds that V1's job was one retrieval and one generation, and a framework
would supply agent loops and tool-calling machinery for a scope the brief
explicitly deferred.

[The V2 brief](../context/brief-v2.md) picks up part of that deferred
scope: three multi-step workflows with tool calls, approvals and effects.
So the question genuinely reopens. It is now two questions:

1. How much agency should the model have over control flow?
2. Should a framework provide it?

## Decision

**A durable deterministic workflow containing bounded model decisions.**
Email drafting, market research and analytics are separate task templates.
A workflow declares its steps up front. The model may fill structured
fields within a step and may choose among capabilities the Run already
carries; workflow code owns the sequence, the transitions, the budgets, the
approvals and the effects.

On the framework question: still hand-written, for the reason ADR-0008 gave
and one new one. The reason that still applies is that the security-
critical logic — the authority intersection, the payload-bound approval —
benefits most from being obvious. The new reason is that the abstraction a
framework would supply here is precisely the planner loop this ADR has just
decided not to want.

## Alternatives considered

| Pattern | What it buys | What it costs | Right when |
|---|---|---|---|
| Deterministic pipeline with model nodes (chosen) | Predictability, replay, clear state, narrow authority per step | Inflexible on tasks nobody wrote a template for | The workflows are known and enumerable — which these three are |
| Single planner agent with typed tools | Flexible decomposition; fewer hard-coded branches | Stochastic trajectories make cost and quality attribution hard; a failed run is hard to explain | Intents vary widely but the tool set and effects stay bounded |
| Supervisor plus specialist agents | Parallelism; isolated contexts per specialist | Coordination tokens, duplicated work, failure multiplication, traces that are hard to read | Independent subtasks measurably benefit from parallelism |
| Fully autonomous multi-agent | Maximum apparent flexibility | Largest security, reliability, observability and cost surface simultaneously | Not justified here, and it is worth saying so plainly |

## Tradeoffs of the chosen option

A template cannot handle a request it was not written for. That is the
whole cost, and it is not small: the third workflow variant a user asks for
is a code change, not a prompt change. Someone will reasonably point out
that a planner agent would have handled it.

The counter is not that planners do not work. It is that their failure mode
is diffuse — a run that took a strange path, cost four times as much and
produced something plausible but wrong is genuinely hard to diagnose, and
harder to prevent recurring. With three known workflows, two of which touch
sensitive data and one of which can send email externally, the trade
currently favours predictability. That balance is a fact about this scope,
not a general claim.

The other real cost is that "bounded model decision" is a line that will be
under pressure. Every step is a candidate for "just let the model decide
this bit", and the boundary only holds if it is defended per step.

## Consequences

- `workflows/base.py` encodes the choice structurally: a `Workflow`
  declares `steps()` up front and receives no planner. A `WorkflowStep`
  reaches the outside world only through the gateway — it gets no settings,
  no database handle, no HTTP client and no model client of its own, so
  every external touch is an authorized, audited contract invocation.
- Workflows declare `required_zones` and `required_scopes`, checked by
  `check_sufficiency()` **before** execution starts. A workflow that would
  be denied on its third step should never have started — especially once
  a reversible write has already landed.
- `StepOutcome` returns the Run rather than mutating it, so a step that
  forgets to propagate the budget charge is a visible mistake at the call
  site rather than a silent reset.
- A step may *request* a status transition (`request_status`) but cannot
  perform one. The Run service decides whether the request is legal from
  the current state.
- **Graduation criteria**, so this is falsifiable rather than a
  preference: move toward more agency when the task corpus shows rigid
  templates causing material failure, when the more agentic path measurably
  improves task success on a representative golden set, and when the
  security and cost regression stays within policy. All three, not one.
- ADR-0008 stays accepted. Nothing here contradicts it; this extends its
  reasoning to a scope it explicitly anticipated.
