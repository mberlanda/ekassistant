"""Mock draft store and email send. EFFECT zone.

Two capabilities with deliberately different effect classes, because the
distinction is the whole control: drafting is the default and is cheap to
undo, sending is a separate capability, a separate scope and an
irreversible act.

`MockEmailSend` records to an in-memory outbox rather than sending
anything, but it is *shaped* like the real thing - it takes an idempotency
key from the invocation context, refuses to send twice under the same key,
and returns a provider message id that the effect ledger stores as the
reconciliation handle. A mock that just returned success would let Phase 3
be written against semantics the real provider does not have.
"""

import time
import uuid
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    InvocationContext,
    Limits,
    ToolContract,
    ToolResult,
    Zone,
)
from ekassistant.capabilities.mocks._shared import mock_result

#: A shape check, not address validation. Deliberately not pydantic's
#: EmailStr: that would add the email-validator dependency to buy
#: RFC-conformance this schema does not need, because the control that
#: actually matters is "is this address on the account's authorized
#: contact list" - a lookup against crm.py, landing with the recipient
#: validator in Phase 3. Syntactic validity is close to irrelevant next to
#: it; a perfectly well-formed address for the wrong person is the failure
#: this workflow exists to prevent.
EmailAddress = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]


class SaveDraftInput(BaseModel):
    account_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    recipients: list[EmailAddress] = Field(min_length=1, max_length=10)


class SaveDraftOutput(BaseModel):
    draft_id: str
    version: int
    saved_at: float


class SendEmailInput(BaseModel):
    draft_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    recipients: list[EmailAddress] = Field(min_length=1, max_length=10)


class SendEmailOutput(BaseModel):
    provider_message_id: str
    accepted_recipients: list[str]
    sent_at: float


SAVE_DRAFT_CONTRACT = ToolContract(
    tool="save_email_draft",
    version=1,
    effect_class=EffectClass.REVERSIBLE_WRITE,
    zone=Zone.EFFECT,
    required_scopes=frozenset({"email.drafts.write"}),
    max_classification=Classification.CONFIDENTIAL,
    input_model=SaveDraftInput,
    output_model=SaveDraftOutput,
    limits=Limits(timeout_ms=3_000, max_rows=1),
    audit_fields=("principal_id", "tenant_id", "purpose", "draft_id", "result_hash"),
    description="Persist a versioned email draft. Reversible; no external delivery.",
)

SEND_EMAIL_CONTRACT = ToolContract(
    tool="send_email",
    version=1,
    effect_class=EffectClass.IRREVERSIBLE_EXTERNAL,
    zone=Zone.EFFECT,
    required_scopes=frozenset({"email.send"}),
    max_classification=Classification.CONFIDENTIAL,
    input_model=SendEmailInput,
    output_model=SendEmailOutput,
    limits=Limits(timeout_ms=15_000, max_rows=1),
    audit_fields=("principal_id", "tenant_id", "purpose", "draft_id", "result_hash"),
    description="Deliver an approved draft externally. Irreversible; requires approval.",
)


class MockDraftStore:
    contract = SAVE_DRAFT_CONTRACT

    def __init__(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}

    def invoke(self, payload: Mapping[str, Any], ctx: InvocationContext) -> ToolResult:
        draft_id = f"draft_{ctx.run_id}"
        existing = self.drafts.get(draft_id)
        version = (existing["version"] + 1) if existing else 1
        self.drafts[draft_id] = {**payload, "version": version, "draft_id": draft_id}

        data = {"draft_id": draft_id, "version": version, "saved_at": time.time()}
        return mock_result(
            SAVE_DRAFT_CONTRACT, data, source_ref=f"drafts://{draft_id}/v{version}"
        )


class SendRefused(Exception):
    """Raised when the provider declines a send outright.

    Distinct from a transport failure: this means the message definitively
    did not go, so no reconciliation is owed. A transport failure would
    surface as an ordinary exception and leave the outcome unknown - see
    CapabilityExecutionError.
    """


class MockEmailSend:
    contract = SEND_EMAIL_CONTRACT

    def __init__(self) -> None:
        self.outbox: list[dict[str, Any]] = []
        self._by_idempotency_key: dict[str, str] = {}
        self.enabled = True

    def invoke(self, payload: Mapping[str, Any], ctx: InvocationContext) -> ToolResult:
        # The kill switch disables sending without disabling drafting -
        # they are separate capabilities precisely so this is possible.
        if not self.enabled:
            raise SendRefused("send capability is disabled by kill switch")

        if ctx.idempotency_key is None:
            raise SendRefused("send requires an idempotency key")

        # Provider-side duplicate suppression, in addition to the ledger's.
        # Both exist because they fail differently: the ledger protects
        # against a retried workflow, this protects against two workflows
        # that both believe they own the same effect.
        previous = self._by_idempotency_key.get(ctx.idempotency_key)
        if previous is not None:
            message_id = previous
        else:
            message_id = f"msg_{uuid.uuid4().hex[:12]}"
            self._by_idempotency_key[ctx.idempotency_key] = message_id
            self.outbox.append(
                {
                    "provider_message_id": message_id,
                    "run_id": ctx.run_id,
                    "principal_id": ctx.principal_id,
                    "recipients": list(payload["recipients"]),
                    "subject": payload["subject"],
                }
            )

        data = {
            "provider_message_id": message_id,
            "accepted_recipients": list(payload["recipients"]),
            "sent_at": time.time(),
        }
        return mock_result(
            SEND_EMAIL_CONTRACT, data, source_ref=f"outbox://messages/{message_id}"
        )
