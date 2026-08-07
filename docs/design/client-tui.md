# Low-level design: Client / UX (TUI)

Reference spine item a. The first iteration of the user-facing client, as
scoped in [the brief](../context/brief.md): "for the first iteration it can
be a simple TUI."

## Responsibilities

1. **Prompt for a question**, send it to the [API Gateway](api-gateway-identity.md)
   as the currently configured mock user (see
   [ADR-0007](../decisions/0007-mock-identity-and-group-lookup.md)),
   optionally switchable per-session for demoing different access levels
   (e.g. a `:user alice` command).
2. **Render the answer** with its citations clearly attached to the claims
   they support, or render the abstain response distinctly from a normal
   answer (so "the system doesn't know" is never visually confusable with
   "the system answered").
3. **Nothing else in V1**: no chat history browsing, no source browsing UI,
   no admin functions — those are all reasonable future additions to this
   layer, not V1 scope.

## Interaction shape

A Read-Eval-Print Loop (REPL): the terminal equivalent of a chat box —
type a question, get a rendered answer, repeat. "TUI" here means a
terminal interface with basic rendering (formatted citations, distinct
abstain styling via a library like `rich`), not a full windowed/widget-based
Text User Interface (that's a plausible V2 step up, e.g. via `textual`, if
richer navigation — multiple panes, history browsing — becomes valuable).

## Tradeoffs

- **TUI over a web UI for V1**: matches the brief's explicit scoping
  ("first iteration...simple TUI") and needs no frontend build tooling at
  all, at the cost of a less shareable/demoable artifact than a web page —
  acceptable since the API Gateway's real HTTP boundary (see
  [api-gateway-identity design](api-gateway-identity.md#tradeoffs)) means a
  web UI later is additive, not a rewrite.
- **REPL/terminal rendering over a full widget-based TUI framework**:
  a plain loop with formatted output (via `rich`) is far less code than a
  framework like `textual`, and V1 has no need for multi-pane layouts or
  persistent on-screen state — this trades away a more polished
  interaction model for something that can be built and used immediately.
- **Citations rendered inline, not as a separate lookup step**: showing
  each citation's source/snippet directly alongside the answer (rather
  than just a chunk ID the user would have to look up) costs a bit more
  terminal output but directly serves the "grounded, cited answers"
  requirement — an answer whose citations require extra steps to verify
  undermines the point of citing them.
