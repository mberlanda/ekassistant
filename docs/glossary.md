# Glossary

Every abbreviation used anywhere in `docs/` or in code comments is defined
here. If you introduce a new one, add it to this table in the same commit.

| Abbreviation | Expansion | Meaning in this project |
|---|---|---|
| ACL | Access Control List | The set of groups/users allowed to see a given document or chunk. Attached as metadata at ingest time and enforced as a pre-filter at query time. See [ADR-0002](decisions/0002-acl-enforcement-at-retrieval.md). |
| ADR | Architecture Decision Record | A short document capturing one architectural decision, its context, and its tradeoffs. See [ADR-0001](decisions/0001-record-architecture-decisions.md). |
| API | Application Programming Interface | Here, specifically the HTTP interface exposed by the API Gateway layer (spine item b). |
| BM25 | Best Matching 25 | A classic keyword/lexical ranking function (a refinement of TF-IDF) used for the keyword half of hybrid search. |
| CDC | Change Data Capture | Detecting inserts/updates/deletes at the source since the last sync, so ingestion can run incrementally instead of re-processing everything. |
| CLI | Command Line Interface | Text-based program invocation, as opposed to a GUI. |
| CRM | Customer Relationship Management | The system of record for accounts, contacts and account history. Read through a typed capability in V2, never queried directly. See [capabilities design](design/capabilities.md). |
| DLP | Data Loss Prevention | Detection/redaction of sensitive data (e.g. secrets, PII) before it is stored or surfaced. |
| GUI | Graphical User Interface | A visual, pointer-driven interface; contrasted with [TUI](#glossary) and [CLI](#glossary) in this project. |
| HNSW | Hierarchical Navigable Small World | The approximate-nearest-neighbor graph index algorithm used internally by most vector databases, including Qdrant. |
| LLM | Large Language Model | The generative model used to compose the final grounded answer (and, at smaller scale, for guardrail/classification tasks). |
| LLD | Low-Level Design | A component-level design document, more implementation-focused than an ADR. See [docs/design/](design/). |
| PDP | Policy Decision Point | The component that answers "is this allowed", separately from the component that enforces the answer. Here, `policy/engine.py`; the enforcement point is the capability gateway. See [policy design](design/policy.md). |
| PII | Personally Identifiable Information | Data that can identify an individual; relevant to the DLP/de-identification concern in the data layer. |
| POC | Proof of Concept | A minimal, working version built to validate an approach, not production-hardened. |
| RAG | Retrieval-Augmented Generation | The overall pattern used here: retrieve relevant grounded context, then have the LLM generate an answer constrained to that context. |
| RBAC | Role-Based Access Control | Access control keyed on roles/groups rather than individual users; the model used by the mock identity lookup table. |
| REPL | Read-Eval-Print Loop | An interactive prompt that reads input, evaluates it, and prints a result, one line/turn at a time; the interaction style of the V1 TUI. |
| RFC | Request For Comments | An internet standards document; referenced only where a schema deliberately does *not* implement one (see the email-address note in `capabilities/mocks/email.py`). |
| RRF | Reciprocal Rank Fusion | The method used to combine dense and keyword search result rankings into one ranked list. See [ADR-0003](decisions/0003-hybrid-retrieval-with-rrf.md). |
| SQL | Structured Query Language | The query language for relational databases. Deliberately kept out of the model/action boundary in V2 — see [the V2 brief](context/brief-v2.md)'s exclusion table. |
| TF-IDF | Term Frequency - Inverse Document Frequency | A statistic scoring how important a word is to a document within a corpus; the basis BM25 refines. |
| TUI | Text User Interface | An interactive terminal interface with layout/widgets, as opposed to a plain [REPL](#glossary) or a [GUI](#glossary). V1 client is a simple TUI/REPL hybrid. |
| V1 | Version 1 | The first scoped milestone of this project, as defined in [the brief](context/brief.md). |
| V2 | Version 2 | The second milestone: acting, not just answering — email drafting, web research and structured analytics. See [the V2 brief](context/brief-v2.md). |
