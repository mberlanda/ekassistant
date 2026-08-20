"""Effect intents, approvals and the ledger.

See docs/design/capabilities.md#effects-and-approval.

The rule this module exists to make mechanical: **an approval is bound to
an exact payload, not to an intent.** Approving "send the client update"
and then sending a different body, a different recipient or a different
attachment is the single most damaging failure available to this system,
and it is not preventable by review discipline alone - a reviewer cannot
see a payload that changed after they clicked approve.

So `approve()` records the hash the reviewer actually saw, and
`authorized_for()` re-hashes the payload at commit time and compares. Any
edit in between invalidates the approval automatically.

What the ledger deliberately does *not* store is the payload itself. It
keeps the hash plus a caller-supplied, audit-safe summary. The ledger is
read by operations, incident review and reconciliation tooling - all
places where a full copy of client email bodies and CRM fields would be a
second, less-guarded home for sensitive data.
"""

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol

from ekassistant.capabilities.contracts import EffectClass, ToolContract, canonical_hash


class EffectStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


#: Once an intent reaches one of these, it is history. Notably COMMITTED
#: is included: a committed effect is never re-approved or re-committed,
#: which is what stops a retried model call from sending a second email.
_SETTLED = frozenset({EffectStatus.COMMITTED, EffectStatus.REJECTED, EffectStatus.COMPENSATED})


class EffectLedgerError(Exception):
    pass


class ApprovalPayloadMismatch(EffectLedgerError):
    """The payload changed between approval and commit.

    Its own exception type rather than a generic error because this is the
    one the effect zone must handle distinctly: it is not a bug and not an
    outage, it is the control working, and the right response is to send
    the Run back for re-approval (see the AWAITING_APPROVAL -> RUNNING
    edge in runs/machine.py).
    """


class EffectNotFound(EffectLedgerError, KeyError):
    pass


@dataclass(frozen=True)
class Approval:
    """A human (or proven policy) decision on one exact payload."""

    approved_by: str
    approved_at: float
    payload_hash: str
    reason: str | None = None


def _new_intent_id() -> str:
    return f"eff_{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class EffectIntent:
    """A proposed side effect, recorded before it happens.

    Written to the ledger *ahead* of execution rather than after, so an
    interrupted Run can tell the difference between "never attempted" and
    "attempted, outcome unknown". Only the second case needs
    reconciliation, and only a pre-write ledger can distinguish them.
    """

    run_id: str
    contract_ref: str
    effect_class: EffectClass
    payload_hash: str
    idempotency_key: str
    payload_summary: Mapping[str, Any] = field(default_factory=dict)
    status: EffectStatus = EffectStatus.PROPOSED
    approval: Approval | None = None
    provider_ref: str | None = None
    detail: str | None = None
    intent_id: str = field(default_factory=_new_intent_id)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def matches(self, payload: Mapping[str, Any]) -> bool:
        return canonical_hash(payload) == self.payload_hash


class EffectLedger(Protocol):
    def propose(
        self,
        run_id: str,
        contract: ToolContract,
        payload: Mapping[str, Any],
        idempotency_key: str,
        payload_summary: Mapping[str, Any] | None = None,
    ) -> EffectIntent: ...

    def approve(
        self,
        intent_id: str,
        approved_by: str,
        payload: Mapping[str, Any],
        reason: str | None = None,
    ) -> EffectIntent: ...

    def reject(self, intent_id: str, rejected_by: str, reason: str) -> EffectIntent: ...

    def mark_committed(self, intent_id: str, provider_ref: str) -> EffectIntent: ...

    def mark_failed(self, intent_id: str, detail: str) -> EffectIntent: ...

    def get(self, intent_id: str) -> EffectIntent: ...

    def find_committed(self, idempotency_key: str) -> EffectIntent | None: ...

    def for_run(self, run_id: str) -> list[EffectIntent]: ...


class InMemoryEffectLedger:
    """Mock ledger. Not durable - the SQLite implementation lands with the
    effect zone in Phase 3.

    Every invariant the real one will enforce is enforced here, because a
    permissive mock would let Phase 3's workflow code be written against
    rules that do not hold in production.
    """

    def __init__(self) -> None:
        self._intents: dict[str, EffectIntent] = {}

    def propose(
        self,
        run_id: str,
        contract: ToolContract,
        payload: Mapping[str, Any],
        idempotency_key: str,
        payload_summary: Mapping[str, Any] | None = None,
    ) -> EffectIntent:
        intent = EffectIntent(
            run_id=run_id,
            contract_ref=contract.ref,
            effect_class=contract.effect_class,
            payload_hash=canonical_hash(payload),
            idempotency_key=idempotency_key,
            payload_summary=dict(payload_summary or {}),
        )
        self._intents[intent.intent_id] = intent
        return intent

    def approve(
        self,
        intent_id: str,
        approved_by: str,
        payload: Mapping[str, Any],
        reason: str | None = None,
    ) -> EffectIntent:
        """Approve the payload the approver was actually shown.

        `payload` is passed in and re-hashed rather than trusting the
        stored hash, so that approving a payload that has already drifted
        from the proposal fails here instead of at commit time - the
        approver gets the error, which is the only place it can be
        resolved.
        """
        intent = self.get(intent_id)
        self._reject_if_settled(intent, "approve")
        if not intent.matches(payload):
            raise ApprovalPayloadMismatch(
                f"payload for {intent.intent_id} does not match the proposed hash"
            )
        return self._store(
            replace(
                intent,
                status=EffectStatus.APPROVED,
                approval=Approval(
                    approved_by=approved_by,
                    approved_at=time.time(),
                    payload_hash=intent.payload_hash,
                    reason=reason,
                ),
            )
        )

    def reject(self, intent_id: str, rejected_by: str, reason: str) -> EffectIntent:
        intent = self.get(intent_id)
        self._reject_if_settled(intent, "reject")
        return self._store(
            replace(
                intent,
                status=EffectStatus.REJECTED,
                detail=f"rejected by {rejected_by}: {reason}",
            )
        )

    def mark_committed(self, intent_id: str, provider_ref: str) -> EffectIntent:
        intent = self.get(intent_id)
        self._reject_if_settled(intent, "commit")
        return self._store(
            replace(intent, status=EffectStatus.COMMITTED, provider_ref=provider_ref)
        )

    def mark_failed(self, intent_id: str, detail: str) -> EffectIntent:
        intent = self.get(intent_id)
        self._reject_if_settled(intent, "fail")
        return self._store(replace(intent, status=EffectStatus.FAILED, detail=detail))

    def get(self, intent_id: str) -> EffectIntent:
        try:
            return self._intents[intent_id]
        except KeyError as exc:
            raise EffectNotFound(intent_id) from exc

    def find_committed(self, idempotency_key: str) -> EffectIntent | None:
        """The duplicate-suppression lookup.

        Callers check this *before* invoking an effectful capability: a
        hit means this exact effect already happened and must not happen
        again, however many times the surrounding model call is retried.
        """
        for intent in self._intents.values():
            if (
                intent.idempotency_key == idempotency_key
                and intent.status is EffectStatus.COMMITTED
            ):
                return intent
        return None

    def for_run(self, run_id: str) -> list[EffectIntent]:
        return [i for i in self._intents.values() if i.run_id == run_id]

    def _store(self, intent: EffectIntent) -> EffectIntent:
        settled = replace(intent, updated_at=time.time())
        self._intents[settled.intent_id] = settled
        return settled

    @staticmethod
    def _reject_if_settled(intent: EffectIntent, action: str) -> None:
        if intent.status in _SETTLED:
            raise EffectLedgerError(
                f"cannot {action} effect {intent.intent_id}: already {intent.status}"
            )


def authorized_for(
    intent: EffectIntent, payload: Mapping[str, Any], *, approval_required: bool
) -> tuple[bool, str]:
    """Whether this intent authorizes executing this exact payload now.

    Returns (ok, reason) rather than raising, because the gateway folds
    this into a single deny path alongside scope, zone and budget checks
    and wants a uniform reason string for all of them.
    """
    if intent.status in _SETTLED:
        return False, f"effect {intent.intent_id} is already {intent.status}"
    if approval_required and intent.status is not EffectStatus.APPROVED:
        return False, f"effect {intent.intent_id} requires approval, is {intent.status}"
    if not intent.matches(payload):
        return False, f"payload does not match the approved hash for {intent.intent_id}"
    return True, "effect intent authorizes this payload"
