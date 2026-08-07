# Low-level design: Governance

Reference spine item i. The policy layer above the data layer: data
classification, Access Control List (ACL) propagation, key management, a
policy engine, and compliance/audit posture.

## Responsibilities

1. **Data classification**: tagging sources/documents by sensitivity
   (e.g. public, internal, restricted) at ingest time, distinct from (but
   related to) ACL group membership — classification is "how sensitive is
   this," ACLs are "who specifically may see this." V1 uses ACL group
   membership as the operative control; classification labels are
   captured as metadata for future policy use but don't yet drive
   different handling.
2. **ACL propagation**: ensuring that when a source's access rules change
   (a group is removed from a document, a document is deleted), that
   change reaches both the vector and keyword indexes promptly via CDC —
   see [ingest design](ingest.md#tradeoffs) and
   [ADR-0002](../decisions/0002-acl-enforcement-at-retrieval.md). This is
   the governance requirement that ingest's delete-propagation mechanism
   exists to satisfy.
3. **Key management**: how source credentials (connector API keys/tokens)
   and any encryption keys are stored and rotated. V1 reads credentials
   from local environment variables / a `.env` file (excluded from version
   control), which is a POC-appropriate stand-in for a real secrets
   manager — see [tradeoffs](#tradeoffs).
4. **Policy engine**: the rules that decide things like "which sources may
   this group's members query" or "does this query type require
   additional approval." V1 has no separate policy engine — ACL group
   membership *is* the policy, enforced directly in the retrieval pre-filter.
   A real policy engine (rules beyond simple group membership) is V2+
   scope.
5. **Compliance and audit**: whether the system's behavior can be shown,
   after the fact, to have respected access boundaries — this is what the
   [audit log](data-layer.md) exists to support, and what the
   [eval harness](observability.md)'s access-control test cases exist to
   continuously verify.

## Tradeoffs

- **ACL group membership as the entire policy model for V1**: no separate
  policy engine means governance logic lives in exactly one place (the
  retrieval pre-filter), which is easy to audit and reason about, but
  cannot express policy beyond "is this group allowed" (e.g. time-based
  access, purpose limitation, per-field redaction). Acceptable for V1's
  scope; flagged as the first thing to reconsider if compliance
  requirements grow beyond simple group-based access.
- **Local `.env`-based credential storage over a secrets manager**:
  standard practice for local development, wrong for anything beyond a
  solo POC — this is explicitly not the production posture and is called
  out here so it's a conscious, documented gap rather than a silent one.
  Real deployment would require a secrets manager (e.g. a cloud KMS-backed
  store) before handling real source credentials.
- **Classification captured but not yet enforced**: recording a
  sensitivity label per document without a policy that acts on it is
  "governance debt" taken on deliberately — it costs nothing to capture
  the metadata now at ingest time, and doing so means V2's policy engine
  doesn't need a data migration to backfill it later.
