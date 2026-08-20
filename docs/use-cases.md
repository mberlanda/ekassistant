# Use cases: what the system does, and where that lives in the code

The other docs answer *why* ([ADRs](decisions/)) and *how a component is
built* ([designs](design/)). This page answers a different question:
**"show me the thing actually happening, and let me follow it through the
code."**

Every row below is a real behaviour with a real entry point, a traceable
path, tests that prove it, and a command you can run. Nothing here is
aspirational — if it is in this file, it is built. What is *designed but
not built* stays in [the roadmap](roadmap.md), never here.

**Reachability** is stated per use case, because it is the single most
confusing thing about the repo right now:

- **V1 use cases are live over HTTP** — `POST /query` against a running API.
- **V2 Phase 1 use cases are live over HTTP** — `POST /runs` and
  `GET /runs/{id}` against the same running API.
- **V2 Phase 0 use cases are still reachable from tests and a Python
  session only.** Phase 0 shipped contracts, a gateway, a policy engine and
  mocks; Phase 1 wired the *Run* half of that into endpoints, but no
  endpoint invokes a capability through the gateway yet. That split is
  deliberate ([the V2 brief](context/brief-v2.md): interfaces and mocks
  first), and Phase 2 is what closes it.

---

## Index

| # | Use case | Reachable via | Zone / area |
|---|---|---|---|
| [UC-1](#uc-1-ask-a-question-get-a-cited-answer) | Ask a question, get a cited answer | HTTP, TUI | V1 query |
| [UC-2](#uc-2-ask-about-something-you-are-not-allowed-to-see) | Ask about something you're not allowed to see | HTTP, TUI | V1 ACL |
| [UC-3](#uc-3-ask-something-the-corpus-cannot-answer) | Ask something the corpus can't answer | HTTP, TUI | V1 abstain |
| [UC-4](#uc-4-ingest-a-corpus-files-and-the-open-web) | Ingest a corpus (files + web) | CLI | V1 ingest |
| [UC-5](#uc-5-revoke-access-or-remove-a-document) | Revoke access / remove a document | CLI | V1 governance |
| [UC-6](#uc-6-measure-whether-it-still-works) | Measure whether it still works | CLI | V1 observability |
| [UC-7](#uc-7-start-a-run-authority-fixed-at-intake) | Start a Run, authority fixed at intake | Python | V2 runs |
| [UC-8](#uc-8-the-same-person-gets-less-authority-for-a-riskier-purpose) | Same person, less authority for a riskier purpose | Python | V2 policy |
| [UC-9](#uc-9-read-internal-data-through-the-gateway) | Read internal data through the gateway | Python | V2 gateway |
| [UC-10](#uc-10-a-research-run-cannot-see-the-crm-zone-denial) | A research Run cannot see the CRM | Python | V2 zones |
| [UC-11](#uc-11-drafting-is-not-sending-scope-denial) | Drafting is not sending | Python | V2 scopes |
| [UC-12](#uc-12-data-above-the-purposes-ceiling-is-refused) | Data above the purpose's ceiling is refused | Python | V2 classification |
| [UC-13](#uc-13-an-approval-is-bound-to-an-exact-payload) | An approval is bound to an exact payload | Python | V2 effects |
| [UC-14](#uc-14-a-send-happens-at-most-once) | A send happens at most once | Python | V2 effects |
| [UC-15](#uc-15-the-kill-switch-stops-sending-without-stopping-drafting) | The kill switch stops sending, not drafting | Python | V2 effects |
| [UC-16](#uc-16-a-looping-model-runs-out-of-budget) | A looping model runs out of budget | Python | V2 runs |
| [UC-17](#uc-17-web-content-arrives-labelled-untrusted) | Web content arrives labelled untrusted | Python | V2 zones |
| [UC-18](#uc-18-reconstruct-what-a-run-did-after-the-fact) | Reconstruct what a Run did, after the fact | Python | V2 audit |
| [UC-19](#uc-19-start-a-run-over-http-and-get-a-cited-answer) | Start a Run over HTTP, get a cited answer | HTTP | V2 runs |
| [UC-20](#uc-20-a-scope-you-do-not-hold-stops-the-work-before-it-starts) | A scope you don't hold stops work before it starts | HTTP | V2 policy |
| [UC-21](#uc-21-someone-elses-run-is-404-never-403) | Someone else's Run is 404, never 403 | HTTP | V2 runs |
| [UC-22](#uc-22-a-run-outlives-the-process-that-created-it) | A Run outlives the process that created it | HTTP | V2 durability |
| [UC-23](#uc-23-a-budget-stops-a-run-between-steps-with-a-named-reason) | A budget stops a Run between steps, with a named reason | HTTP | V2 runs |

**Two minutes of setup** covers UC-1 through UC-6:

```bash
make install && make up          # deps + Qdrant
make models                      # pull llama3.2:1b + nomic-embed-text
make ingest                      # build both indexes from seed_corpus/
make api                         # in one terminal
make tui                         # in another
```

UC-7 onward need none of that — no Qdrant, no Ollama, no server:

```bash
.venv/bin/python                 # then paste any V2 snippet below
```

The four mock users referenced throughout come from
[`config/identities.yaml`](../config/identities.yaml)
([ADR-0007](decisions/0007-mock-identity-and-group-lookup.md)):

| User | Groups | Shorthand for |
|---|---|---|
| `alice` | `engineering`, `all-staff` | an engineer |
| `bob` | `support`, `all-staff`, `client-comms` | support, and the only one who can send mail |
| `carol` | `finance`, `all-staff` | finance |
| `guest` | *(none)* | someone with no entitlements at all |

---

# V1 — answering questions

## UC-1: Ask a question, get a cited answer

**What happens.** Alice asks whether VPN access requires MFA. The system
retrieves only chunks her groups can see, hands them to the model under a
cite-or-abstain contract, rebuilds the citations from the authoritative
context (never from the model's own output), and answers.

**Run it.**

```bash
curl -s localhost:8000/query -H 'content-type: application/json' \
  -H 'x-user-id: alice' \
  -d '{"question":"Does VPN access require MFA?"}' | jq
```

**Follow it through the code.**

| Step | Code |
|---|---|
| Route, identity resolution, keyword index constructed *inside the handler body* | `src/ekassistant/api/main.py:103` (`query`) |
| Groups for the caller | `src/ekassistant/identity/store.py` |
| Two-stage pipeline: retrieve, then generate | `src/ekassistant/orchestration/pipeline.py:34` (`answer_question`) |
| Hybrid search + fusion + rerank | `src/ekassistant/retrieval/retriever.py:47` (`retrieve`) → `rrf.py`, `reranker.py` |
| Grounded generation, citations rebuilt from context | `src/ekassistant/models/generation.py:85` (`generate_answer`) |
| One JSON line per call | `src/ekassistant/observability/tracing.py` (`record_run`) |

> The keyword index is built inside the route handler's own function body,
> **not** via FastAPI `Depends()`. That is load-bearing, not style: a sync
> dependency resolves through its own threadpool dispatch, which is not
> guaranteed to be the same OS thread as the handler body, and sqlite3
> refuses a connection used across threads. This was a real, reproduced
> bug (16/20 concurrent requests failing). See
> `tests/test_api_query.py:289`.

**Proof.** `tests/test_api_query.py:89`, `tests/test_orchestration_pipeline.py`,
`tests/test_retriever.py`, `tests/test_generation.py`.

**Design.** [orchestration.md](design/orchestration.md) ·
[retrieval.md](design/retrieval.md) · [model-layer.md](design/model-layer.md)

---

## UC-2: Ask about something you are not allowed to see

**What happens.** Carol (finance) asks a question whose answer lives in
support-only content. She does not get a filtered-down answer or a
"redacted" one — the content is **never retrieved in the first place**, so
she gets weak matches from her own documents and an abstain. Guest, with
no groups, gets nothing at all.

**Run it.**

```bash
curl -s localhost:8000/query -H 'content-type: application/json' \
  -H 'x-user-id: carol' \
  -d '{"question":"What is the refund macro for a duplicate charge?"}' | jq
curl -s localhost:8000/query -H 'content-type: application/json' \
  -H 'x-user-id: guest' -d '{"question":"Does VPN access require MFA?"}' | jq
```

**Where the control actually is.** Not in the API, not in the orchestrator,
not in the prompt — inside each index adapter, as a native filter in the
query itself:

| Index | Mechanism | Code |
|---|---|---|
| Vector | Qdrant `MatchAny` payload filter | `src/ekassistant/index/vector_index.py` |
| Keyword | SQL join against `chunk_groups` | `src/ekassistant/index/keyword_index.py` |

Both `search()` calls **fail closed on an empty group set** — no groups
means no results, never all results. `retrieve()`'s only ACL
responsibility is to always thread the caller's real `allowed_groups`
through and never default it (`retrieval/retriever.py:47`).

**Proof.** `tests/test_retriever.py:45`, `tests/test_retriever.py:62`,
`tests/test_api_query.py:210`, plus the multi-group partial-overlap cases
in `tests/test_vector_index.py` and `tests/test_keyword_index.py`.

**Design.** [ADR-0002](decisions/0002-acl-enforcement-at-retrieval.md) ·
[retrieval.md](design/retrieval.md)

---

## UC-3: Ask something the corpus cannot answer

**What happens.** The model is required to cite, and every citation it
returns is validated against the chunks that were actually retrieved. A
plausible-sounding answer with no valid citation is downgraded to an
abstain, and the *reason* is recorded — six named reasons, not a flat
counter:

`empty_context` · `malformed_response` · `model_reported_abstain` ·
`blank_answer` · `no_citations` · `invalid_citation`

**Follow it.** `src/ekassistant/models/generation.py:37` (the `REASON_*`
constants), `:107` (`_validate_citations`). The reasons are deliberately
kept **out of the LLM-facing JSON schema** so they cannot confuse the
model's constrained output.

**Worth knowing.** `llama3.2:1b` is genuinely unreliable at this contract
run-to-run — that is documented, expected behaviour
([ADR-0004](decisions/0004-local-llm-serving-via-ollama.md)), not a
pipeline bug. An infrastructure failure (Ollama down) surfaces as an HTTP
500, never a false-positive abstain: `tests/test_api_query.py:271`.

**Proof.** `tests/test_generation.py`, `tests/test_api_query.py:114`,
`tests/test_api_query.py:191`.

---

## UC-4: Ingest a corpus (files and the open web)

**What happens.** Every configured connector is combined into a *single*
ingest run, documents are parsed to a common `#`-heading convention,
chunked on structure, embedded, ACL-tagged and written to both indexes.

**Run it.** `make ingest`

**Follow it.**

| Step | Code |
|---|---|
| CLI entry | `src/ekassistant/ingest/cli.py` |
| One run over all connectors | `src/ekassistant/ingest/connectors/composite.py` |
| Local files, manifest-driven | `connectors/filesystem.py` + `seed_corpus/manifest.yaml` |
| Web crawl, domain-agnostic | `connectors/crawler.py` + [`config/crawl_targets.yaml`](../config/crawl_targets.yaml) |
| Format adapters | `parsers/{text,html,pdf,docx}_parser.py` |
| Structure-aware chunking | `ingest/chunker.py` |
| Dual-index write + delete diff | `ingest/pipeline.py` (`run_ingest`) |

> `CompositeConnector` is not a convenience wrapper. Calling `run_ingest()`
> once per connector made each connector's blind spot look like a mass
> deletion to the delete-diff logic — the crawler's empty config wiped the
> entire seed corpus. The single-call contract is documented in
> `pipeline.py` and regression-tested both ways.

> `escape_accidental_heading` in `parsers/_shared.py` exists because
> uncontrolled prose that happens to begin `"# "` — a pasted heading, a
> ticket reference — was being silently read as a real document heading.

**Proof.** `tests/test_ingest_pipeline.py`, `tests/test_composite_connector.py`,
`tests/test_parsers.py`, `tests/test_chunker.py`,
`tests/test_crawler_connector.py`.

**Design.** [ingest.md](design/ingest.md) ·
[ADR-0009](decisions/0009-ingest-format-adapters.md) ·
[ADR-0010](decisions/0010-web-crawler-connector.md)

---

## UC-5: Revoke access, or remove a document

**What happens.** Ingest diffs each source's live chunk ids against what
is already indexed — the union of what *both* indexes separately report,
so pre-existing drift self-heals — and deletes the difference from both.

This is an **authorization** control, not a tidiness one. Before it
existed, revoking a group's access left that group able to retrieve the
content indefinitely.

**Run it.** Remove an entry from `seed_corpus/manifest.yaml`, re-run
`make ingest`, and the chunk count drops by exactly that document's count.

**Follow it.** `ingest/pipeline.py` (the delete-diff), backed by
`chunk_ids_for_source` / `all_sources` on both index adapters.

**Proof.** `tests/test_governance.py:83` (revoke-and-reingest reaches both
indexes), `:113` (full removal), `:136` (an unrelated document survives).
These run live against real Qdrant + SQLite, including a 300-chunk case
that forces Qdrant scroll pagination across pages.

**Design.** [governance.md](design/governance.md)

---

## UC-6: Measure whether it still works

**What happens.** Two tiers, deliberately different in kind:

- **Tier 1 — deterministic.** Every mock user × every seed-corpus document.
  No LLM involved, so a failure is always a real regression.
- **Tier 2 — measurement, not assertion.** Abstain rate and the *breakdown
  of why*, over repeated runs of a fixed answerable question. It does not
  fail the suite, because `llama3.2:1b`'s flakiness is documented expected
  behaviour, not a regression.

**Run it.** `make eval`, and read `data/traces.jsonl`.

**Follow it.** `src/ekassistant/observability/eval_harness.py`,
`eval_cli.py`, `tracing.py`.

> Tracing appends one JSON line per query and takes no lock, relying on
> the POSIX `O_APPEND` + `PIPE_BUF` atomicity guarantee — verified with 200
> concurrent writes, 0 lost or corrupted lines.

**Design.** [observability.md](design/observability.md)

---

# V2 Phase 0 — the machinery for doing work

Everything in *this* section is reachable **only from a Python session or
the test suite** — these are the authorization primitives, and Phase 2 is
what puts them behind an endpoint. (The Run lifecycle they feed *is* live
over HTTP; see [V2 Phase 1](#v2-phase-1--runs-over-http) below.) Start
every snippet in this section with this preamble:

```python
from pathlib import Path
from ekassistant.capabilities.audit import InMemoryAuditSink
from ekassistant.capabilities.bootstrap import build_registry
from ekassistant.capabilities.effects import InMemoryEffectLedger
from ekassistant.capabilities.gateway import CapabilityGateway
from ekassistant.policy.engine import ScopePolicyEngine
from ekassistant.runs.intake import IntakeRequest, create_run

policy  = ScopePolicyEngine.from_yaml(Path("config/policies.yaml"))
ledger  = InMemoryEffectLedger()
audit   = InMemoryAuditSink()
registry = build_registry()
gateway = CapabilityGateway(registry, policy, ledger, audit)
```

The one sentence the whole of V2 hangs off:

> **Authority is carried, never generated.** A model may choose among
> capabilities a Run already holds. It cannot add a scope, a recipient, a
> destination, a data class or an effect class.

Effective permission at any moment is an intersection —

```
caller permission ∩ tenant policy ∩ Run purpose and scopes
                  ∩ tool policy ∩ data classification
```

— and the code that computes it is `CapabilityGateway.invoke`
(`src/ekassistant/capabilities/gateway.py:179`). Read that one method and
you have read the security model. Its check order is not arbitrary:

| Order | Check | Why here |
|---|---|---|
| 1 | Run is terminal? | A finished Run does nothing more |
| 2 | Budget would exhaust? | Checked **before**, charged **immediately after** — so denials cost budget too (UC-16) |
| 3 | Capability exists? | Unknown tool is a denial, not a crash |
| 4 | Zone / scope / classification (`_check_authority:306`) | The three things the Run must *already carry* |
| 5 | Policy decision (`policy/engine.py:141`) | Purpose ∩ effect class ∩ ceiling ∩ scopes |
| 6 | Payload validates against the contract | Malformed input never reaches the tool |
| 7 | Effect intent + approval hash | Only for effectful contracts |
| 8 | Invoke, validate output, commit intent, audit | |

> **`invoke()` returns a denial; it does not raise.** The Run is charged
> for the attempt either way, and an exception path made it far too easy
> to drop the charged copy. Call `.unwrap()` when you want the raising
> behaviour (`gateway.py:101`).

---

## UC-7: Start a Run, authority fixed at intake

**What happens.** `create_run` takes `groups` + `purpose` and *returns*
scopes and zones. A caller cannot pass `approved_scopes` even if it wants
to — the field is not on `IntakeRequest`. That is the entire mechanism
behind "authority is carried, never generated": there is exactly one
constructor, and it derives authority from policy rather than accepting it.

```python
run = create_run(IntakeRequest(
    tenant_id="acme", principal_id="bob",
    groups=["support", "all-staff", "client-comms"],
    purpose="client_communication", task_type="email_draft"), policy)

sorted(run.allowed_zones)     # PURE, INTERNAL_DATA, EFFECT
sorted(run.approved_scopes)   # crm.accounts.read, email.drafts.write, email.send, ...
[c.ref for c in gateway.visible_contracts(run)]
```

An unknown purpose, or a task type the purpose does not cover, raises
`IntakeRejected` carrying the `PolicyDecision` — a reason and a policy
version, not a bare 403.

**Code.** `src/ekassistant/runs/intake.py:46` (`create_run`) ·
`runs/aggregate.py:114` (the frozen `Run`) · `runs/machine.py:120`
(`transition`, the only legal way a Run changes state).

**Proof.** `tests/test_run_intake.py:23` (the request literally cannot
carry authority), `:46`, `:53`, `:59` (guest gets a Run that can do
nothing), `:95`.

**Design.** [runs.md](design/runs.md) ·
[ADR-0011](decisions/0011-durable-run-aggregate.md)

---

## UC-8: The same person gets less authority for a riskier purpose

**What happens.** Bob holds `email.send` through `client-comms`. Doing
market research, he carries a strictly smaller Run — **without holding two
accounts**. Purpose narrows what group membership grants; it never widens
it.

```python
research = create_run(IntakeRequest(
    tenant_id="acme", principal_id="bob",
    groups=["support", "all-staff", "client-comms"],
    purpose="market_research", task_type="market_research"), policy)

sorted(research.allowed_zones)                            # PURE, WEB_RESEARCH
[c.ref for c in gateway.visible_contracts(research)]      # ['web_search@1']
```

He still *holds* `email.send` as a scope. It reaches nothing, because the
zone is not on the Run.

**Where the decision lives.** [`config/policies.yaml`](../config/policies.yaml)
— and the thing to read it for is what is **absent**:

| Purpose | Zones | Ceiling | Deliberately missing |
|---|---|---|---|
| `knowledge_qa` | PURE, INTERNAL_DATA | CONFIDENTIAL | web, effects |
| `client_communication` | PURE, INTERNAL_DATA, EFFECT | CONFIDENTIAL | **web** — a client draft never holds untrusted text |
| `market_research` | PURE, WEB_RESEARCH | PUBLIC | **internal data and effects** — injected instructions have nothing to reach |
| `internal_analytics` | PURE, INTERNAL_DATA | RESTRICTED | web, effects; the only purpose reaching RESTRICTED |

No purpose combines private data, untrusted web content and an outbound
channel. That is the whole of
[ADR-0013](decisions/0013-execution-zone-isolation.md).

**Proof.** `tests/test_run_intake.py:64`, `:80`, and
`tests/test_policy_engine.py:55` — which asserts the separation against the
**shipped** config file, not a synthetic fixture. A synthetic-policy test
passes cheerfully while the deployed config has a hole in it.

---

## UC-9: Read internal data through the gateway

```python
run = create_run(IntakeRequest(
    tenant_id="acme", principal_id="bob",
    groups=["support", "all-staff", "client-comms"],
    purpose="client_communication", task_type="email_draft"), policy)

out = gateway.invoke(run, "crm_account_lookup", {"account_id": "acct-1001"})
out.allowed          # True
out.unwrap().data    # the account, with authorized contacts
out.unwrap().provenance
run = out.run        # <- propagate the charged Run, always
```

`out.run` is not optional bookkeeping. The gateway charges the Run it was
given and hands back a charged copy; a caller that drops it silently
resets the spend. That is why `StepOutcome` returns the Run too
(`workflows/base.py`) — dropping it becomes a visible mistake at the call
site.

**The mocks are strict on purpose.** An unknown account **raises** rather
than returning an empty record, because an empty account reads downstream
as a *permission* result. An uncertified metric period refuses rather than
interpolating. A permissive stub would let later phases be written against
semantics the real thing does not have.

**Code.** `capabilities/mocks/crm.py`, `metrics.py` ·
`capabilities/contracts.py:117` (`ToolContract`) ·
`capabilities/registry.py:76` (`visible_to`).

**Proof.** `tests/test_capability_gateway.py:37`,
`tests/test_capability_mocks.py:35`, `:44`, `:63`.

**Design.** [capabilities.md](design/capabilities.md) ·
[ADR-0012](decisions/0012-capability-registry-and-gateway.md)

---

## UC-10: A research Run cannot see the CRM (zone denial)

```python
out = gateway.invoke(research, "crm_account_lookup", {"account_id": "acct-1001"})
out.allowed   # False
out.reason
# "crm_account_lookup@1 runs in zone INTERNAL_DATA, which this run does
#  not carry ([PURE, WEB_RESEARCH])"
```

Denied **even though Bob holds `crm.accounts.read`**. Zone is checked
before scope, and a scope cannot buy its way into a zone the Run does not
have. The capability is not merely blocked — it was never in
`visible_contracts(research)` to begin with, so a model driving this Run
never learns it exists.

**Proof.** `tests/test_capability_gateway.py:79` (the name says it: *zone
denial even when the scope is held*), `:63`,
`tests/test_capability_registry.py:108`.

---

## UC-11: Drafting is not sending (scope denial)

```python
carol = create_run(IntakeRequest(
    tenant_id="acme", principal_id="carol",
    groups=["finance", "all-staff"],
    purpose="client_communication", task_type="email_draft"), policy)

gateway.invoke(carol, "save_email_draft", {...}).allowed   # True
gateway.invoke(carol, "send_email", {...}).allowed         # False — missing email.send
```

`client-comms` is its own group rather than a scope bundled into
`support`, so *who can send externally* is a visible membership question
rather than something buried in a scope list.

**Proof.** `tests/test_capability_gateway.py:89`,
`tests/test_policy_engine.py:129` (denials name the missing scope).

---

## UC-12: Data above the purpose's ceiling is refused

Classification is an ordered ceiling (`contracts.py:88`), not an equality
test: a CONFIDENTIAL-ceiling Run reads PUBLIC and INTERNAL fine, and is
refused RESTRICTED. `internal_analytics` is the only purpose that reaches
RESTRICTED at all.

**Proof.** `tests/test_capability_gateway.py:98`, `:110`,
`tests/test_policy_engine.py:122`, `tests/test_run_aggregate.py:39`.

---

## UC-13: An approval is bound to an exact payload

**The single most important control in V2.** An approval authorizes *one
payload*, identified by hash. Change the recipient after approval and the
send is refused — the approval does not transfer.

```python
SEND = {"draft_id": "d-1", "recipients": ["client@acme.example"],
        "subject": "Q3 summary", "body": "..."}

intent = gateway.propose_effect(run, "send_email", SEND, idempotency_key="send-1")

gateway.invoke(run, "send_email", SEND, effect_intent_id=intent.intent_id).reason
# "... requires approval"

ledger.approve(intent.intent_id, "bob", gateway.canonical_payload("send_email", SEND))

tampered = {**SEND, "recipients": ["analyst@competitor.example"]}
gateway.invoke(run, "send_email", tampered, effect_intent_id=intent.intent_id).reason
# "... does not match the approved hash"
registry.get("send_email").outbox      # [] — nothing left the building
```

> **The hash is over the *validated* payload, not the raw input.** Pydantic
> coerces, so the raw dict and what the tool actually receives can differ —
> binding an approval to the raw form would approve something other than
> what executes. That is precisely why `canonical_payload()` is public and
> `propose_effect()` exists, rather than callers reaching into the ledger
> themselves.

The ledger stores a **hash and a summary, never the payload**
(`effects.py:82`).

**Also refused:** an intent belonging to another Run; an intent for a
different contract; an unknown intent; an effect intent passed to a
read-only capability; an already-committed intent reused.

**Code.** `gateway.py:128` (`canonical_payload`), `:144`
(`propose_effect`), `:325` (`_resolve_intent`) ·
`capabilities/effects.py`.

**Proof.** `tests/test_capability_gateway.py:189`, `:207`, `:222`, `:233`,
`:242`, `:267`, `:276` · `tests/test_effects.py:63`, `:73`, `:83`, `:97`,
`:110`.

---

## UC-14: A send happens at most once

Every effect intent carries an idempotency key. A committed key blocks a
second send, and a settled intent (committed or rejected) never authorizes
again. An *uncertain* send — the provider neither confirmed nor clearly
failed — deliberately leaves the intent unsettled for reconciliation
rather than guessing; `IRREVERSIBLE_EXTERNAL` is in `NEVER_BLIND_RETRY`
(`contracts.py:47`).

**Proof.** `tests/test_capability_gateway.py:248`, `:319` ·
`tests/test_effects.py:110`, `:125` ·
`tests/test_capability_mocks.py:110`, `:126`.

---

## UC-15: The kill switch stops sending without stopping drafting

A deployment-level switch refuses sends while every other capability keeps
working — the point being that stopping the irreversible thing must not
require stopping the system.

**Proof.** `tests/test_capability_gateway.py:283`,
`tests/test_capability_mocks.py:139`.

---

## UC-16: A looping model runs out of budget

**What happens.** Budget is checked before the call and charged
immediately after — **including on denials**. A model looping on a
forbidden tool runs out of budget rather than looping for free.

```python
from ekassistant.runs.aggregate import Budget
tiny = create_run(IntakeRequest(..., budget=Budget(max_steps=2)), policy)
```

`would_exhaust` names the dimension that breaks (`aggregate.py:192`)
rather than returning a bare boolean, so the terminal reason can say
*which* budget ran out. `charge` does not enforce — enforcement is the
caller's, checked separately — and `Run` is frozen, so every mutation
returns a new instance. Phase 1 is where that check stopped being every
caller's problem: see [UC-23](#uc-23-a-budget-stops-a-run-between-steps-with-a-named-reason).

**Proof.** `tests/test_capability_gateway.py:143`, `:155` (*denied attempts
still consume budget*) · `tests/test_run_aggregate.py:46`, `:54`, `:63`,
`:73`, `:108`.

> **Known limitation:** there is no timer service, so `deadline_ts` is
> checked on access rather than firing. A Run that stalls *between* calls
> will not time itself out. Tracked in [the roadmap](roadmap.md).

---

## UC-17: Web content arrives labelled untrusted

Results from the WEB_RESEARCH zone carry `is_untrusted`
(`contracts.py:188`) as a property of their **provenance**, not a
heuristic over their text. The mock ships a deliberately hostile fixture —
a page containing embedded instructions — so later phases have something
adversarial to test against from day one; it seeds the Phase 4 injection
red-team corpus.

The zone boundary is the real control, not the label: a
`market_research` Run holds no INTERNAL_DATA and no EFFECT, so injected
instructions have nothing to read and nowhere to send. The only crossing
between zones is a structured evidence bundle with sources and hashes —
never raw text into a model's context.

**Registry-enforced invariants:** the PURE zone cannot host an effect, and
the WEB zone cannot host a write. These are validated at registration, so
a badly-declared contract fails at startup rather than at request time
(`registry.py:105`).

**Proof.** `tests/test_capability_mocks.py:72`, `:83` ·
`tests/test_capability_gateway.py:55` ·
`tests/test_capability_registry.py:145`, `:152`.

---

## UC-18: Reconstruct what a Run did, after the fact

Every invocation — allowed *and* denied — produces an `InvocationRecord`:
run, tenant, principal, contract ref, decision and reason, policy version,
latency, payload hash, result hash, effect intent id.

**Hashes, never payloads.** An audit trail that stores the content becomes
the thing you have to protect.

```python
for rec in audit.for_run(run.run_id):
    print(rec.contract_ref, rec.allowed, rec.reason)
```

`JsonlAuditSink` (`audit.py:69`) writes the same records to a file, using
the same append-atomicity property as V1 tracing (UC-6).

The `policy_version` on every record is what lets an incident review ask
*"would this still be permitted today?"* separately from *"was it
permitted then?"* — which is why `config/policies.yaml` carries an
explicit version to bump.

**Proof.** `tests/test_capability_gateway.py:37`, `:304` (a failing
capability still keeps the charge **and** the audit record).

---

---

# V2 Phase 1 — Runs over HTTP

Phase 1 is where a Run stops being a Python object and becomes a durable,
addressable thing. `POST /runs` starts one, `GET /runs/{id}` reads its
status back, and V1's RAG pipeline is re-expressed as one *task type*
(`knowledge_qa`) rather than a special-cased endpoint — so retrieval
becomes one kind of work a Run can do, not the only kind the system knows.

`POST /query` is byte-for-byte unchanged and still works. The two coexist
on purpose: V1 stays a fair baseline to measure the V2 path against, and
`knowledge_qa`'s one step calls the same `answer_question()` V1 calls
(`workflows/knowledge_qa.py:89`), so a difference between them is a
difference in *authorization*, never in retrieval quality.

No extra setup — the same `make api` server serves these.

## UC-19: Start a Run over HTTP and get a cited answer

**What happens.** Intake fixes the Run's authority from purpose + groups
(UC-7), the workflow runs, and the answer comes back **once**. The Run
record persists; the answer text does not.

```bash
curl -s localhost:8000/runs -H 'X-User-Id: alice' -H 'Content-Type: application/json' \
  -d '{"purpose":"knowledge_qa","task_type":"knowledge_qa",
       "input":{"question":"What is the deployment process?"}}' | jq
```

The response has two halves: `run` (durable) and `result` (a one-time
echo). `CreateRunResponse` (`api/main.py:368`) says why — `RunResponse`
carries status, budget, spend and `evidence_refs`, but **never the answer
text**, so a later `GET /runs/{id}` returns the record without the content.
That is the same trust boundary V1's `/query` already draws; Phase 1 does
not widen it just because there is now somewhere to persist things.

The path: `create_run_route` (`api/main.py:392`) validates input *before*
`run_store.create()` — so a 400 or 422 leaves no orphaned `RECEIVED` Run —
then `execute_workflow` (`runs/service.py:76`) drives
`RECEIVED → PLANNED → RUNNING → VALIDATING → COMPLETED`, saving after every
transition so a concurrent `GET` sees live progress rather than a stale
`RECEIVED` Run that silently finished elsewhere.

**Proof.** `tests/test_api_runs.py:289` (*completes and is returned once*) ·
`:317` (*20 genuinely concurrent requests, real threads*) ·
`tests/test_workflow_knowledge_qa.py:109`, `:124` (*an abstain still
completes the Run*), `:140` (*infrastructure failure propagates rather than
faking an abstain*).

---

## UC-20: A scope you do not hold stops the work before it starts

**What happens.** `guest` holds no `kb.documents.read`, so the Run is
rejected **before a single retrieval call runs** — not after retrieving and
filtering everything away.

```bash
curl -s localhost:8000/runs -H 'X-User-Id: guest' -H 'Content-Type: application/json' \
  -d '{"purpose":"knowledge_qa","task_type":"knowledge_qa","input":{"question":"anything"}}' | jq '.run.status, .run.terminal_reason'
# "REJECTED_POLICY"  "missing scopes ['kb.documents.read']"
```

Two things worth noticing. First, this is **201, not 403** — the refusal
*is* the outcome, and a refused Run is a real, auditable record rather than
an HTTP error with nothing behind it. An HTTP error is reserved for
requests where no valid Run could be created at all (unknown purpose → 403,
unregistered task type → 400, missing input → 422). Second, the check is
`check_sufficiency` (`workflows/base.py:115`), called by `execute_workflow`
*before* anything is charged or attempted (`runs/service.py:120`): a Run
that would be denied on its third step should never have run the first two.

This also gives `kb.documents.read` its first real teeth. The scope has
been in `config/policies.yaml` since Phase 0 and was checked by nothing —
it is now enforced ahead of V1's per-chunk ACL filter, which still runs
underneath (UC-2). Two independent layers, not one moved.

**Proof.** `tests/test_api_runs.py:184` · `tests/test_workflow_knowledge_qa.py:161` ·
`tests/test_run_service.py:172` (*rejects before running anything*).

---

## UC-21: Someone else's Run is 404, never 403

**What happens.** Fetching a Run belonging to another principal or another
tenant returns exactly what a `run_id` that never existed returns.

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/runs/<alices-run-id> -H 'X-User-Id: bob'
# 404
```

A 403 would confirm the `run_id` exists and is merely not yours — which is
itself information a caller with no legitimate access should not get. This
is the same reasoning as UC-2's "no leakage" denial, applied to Run
identifiers instead of document content. `get_run_route`
(`api/main.py:514`) checks `tenant_id` *and* `principal_id`, and the store's
`list_for_principal` (`runs/sqlite_store.py:238`) makes tenant part of the
key rather than a post-filter, so a caller that forgets to pass it gets
nothing rather than everything.

**Proof.** `tests/test_api_runs.py:260` (*another principal's Run is 404, not
403*) · `:253` · `tests/test_run_store_sqlite.py:112`, `:121` (*wrong tenant
returns nothing rather than everything*).

---

## UC-22: A Run outlives the process that created it

**What happens.** Restart the API and the Run is still there — Phase 0's
`InMemoryRunStore` is no longer what serves requests.

```bash
RUN_ID=$(curl -s localhost:8000/runs -H 'X-User-Id: alice' -H 'Content-Type: application/json' \
  -d '{"purpose":"knowledge_qa","task_type":"knowledge_qa","input":{"question":"anything"}}' | jq -r .run.run_id)
# restart the server, then:
curl -s localhost:8000/runs/$RUN_ID -H 'X-User-Id: alice' | jq '.status, .spend'
```

`SqliteRunStore` (`runs/sqlite_store.py:168`) implements the same
`RunStore` Protocol as the in-memory one and rejects everything it rejects,
so no caller was written against a more permissive contract than it gets.
It handles sqlite's thread affinity differently from `SqliteKeywordIndex`
(whose cross-thread bug is the reason this is called out at all — see
UC-1's note): it **never holds a connection across calls**, opening and
closing one per method via `_session` (`:176`), which is what makes it safe
to cache as an `lru_cache` singleton.

**Proof.** `tests/test_run_store_sqlite.py:135` (*a second store instance over
the same path sees the same data*) · `:38` (*round trip preserves every field
group*) · `:145`, `:176` (*real OS threads, not a sequential loop*).

> **Known limitation:** the optimistic-concurrency check compares
> wall-clock `updated_at`, not a version token tied to what a caller
> actually read, so two callers racing from the same stale snapshot can
> both pass it. `tests/test_run_store_sqlite.py:88` asserts what the store
> genuinely promises ("no torn write") rather than a guarantee it does not
> give. Documented in [runs.md](design/runs.md) and [the roadmap](roadmap.md).

---

## UC-23: A budget stops a Run between steps, with a named reason

**What happens.** UC-16 showed budget being *checked*. Phase 1 is where the
check is wired to the state machine, so exhaustion actually stops a Run and
records **which** budget ran out.

```bash
curl -s localhost:8000/runs -H 'X-User-Id: alice' -H 'Content-Type: application/json' \
  -d '{"purpose":"knowledge_qa","task_type":"knowledge_qa","input":{"question":"anything"},
       "budget":{"max_steps":0}}' | jq '.run.status, .run.terminal_reason, .result'
# "BUDGET_EXHAUSTED"  "max_steps exhausted before charging steps=1 ..."  null
```

`charge_or_exhaust` (`runs/service.py:43`) is the wire Phase 0 left
unconnected: `would_exhaust()` *reported* which dimension breaks,
`machine.transition()` *knew how* to terminate with a reason, and nothing
called one from the other. The non-obvious part is that only `"deadline"`
maps to `TIMED_OUT` (`runs/service.py:36`) — everything else maps to
`BUDGET_EXHAUSTED` — so in a corpus of stopped Runs, "stalled" stays
distinguishable from "did a lot of work". Exhaustion is checked *between*
steps, never mid-step, so a Run stops cleanly rather than partway through.

Note `result` is `null`: a Run that stopped produced no answer, and the
response says so rather than returning a partial one.

**Proof.** `tests/test_run_service.py:37`, `:47`, `:55` (*each dimension
separately*) · `:63` (*a passed deadline maps to TIMED_OUT, not
BUDGET_EXHAUSTED*) · `:77` (*never returns a Run missing a terminal reason*) ·
`:188` (*stops between steps, not mid-step*) · `tests/test_api_runs.py:212`,
`:235`.

> **Known limitation:** `POST /runs`' budget override is caller-supplied and
> not bounded by policy — a caller may currently ask for a larger budget
> than their tenant should allow. Tracked in [the roadmap](roadmap.md).

## Not built yet

So this page stays trustworthy, the things people most often assume exist:

| Not built | Where it's tracked |
|---|---|
| The capability gateway over HTTP — `POST /runs` is live, but no endpoint invokes a capability through it yet | Roadmap Phase 2 |
| Durable effect ledger (in-memory; the Run store *is* durable as of Phase 1) | Phase 3 |
| Real email, CRM, metrics or search providers (all mocks) | Phases 3–5 |
| Delegation chain — on-behalf-of token, consent record, expiry | Seam named in [runs.md](design/runs.md), unpopulated |
| Timer-driven Run timeout | See UC-16 |
| Natural-language-to-SQL | Excluded, with graduation criteria in [the V2 brief](context/brief-v2.md) |

For what is planned and in what order, see
[the roadmap](roadmap.md#v2-acting-not-just-answering).
