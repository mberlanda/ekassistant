"""The capability gateway: the single place authority is decided.

See docs/design/capabilities.md#gateway and ADR-0012.

Effective permission is the intersection of five things:

    Run authority  ∩  current policy  ∩  contract requirements
                   ∩  data classification  ∩  effect approval

Not the union, and not whichever is most convenient. Two of those are
snapshots (what the Run was granted at intake) and one is live (what
policy says right now), which is why both are checked - a Run in flight
when a group is revoked must lose the capability, and a policy loosened
mid-run must not retroactively widen a Run that was created under a
narrower one.

Everything here is fail-closed: every check denies by default and only an
explicit pass allows. There is no bypass parameter, no privileged caller
and no "internal" flag - a caller that needs more authority needs a
different Run.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ekassistant.capabilities.audit import AuditSink, InvocationRecord
from ekassistant.capabilities.contracts import (
    REQUIRES_APPROVAL,
    REQUIRES_EFFECT_INTENT,
    InvocationContext,
    ToolContract,
    ToolResult,
    canonical_hash,
)
from ekassistant.capabilities.effects import (
    EffectIntent,
    EffectLedger,
    EffectNotFound,
    authorized_for,
)
from ekassistant.capabilities.registry import CapabilityNotFound, CapabilityRegistry
from ekassistant.policy.engine import PolicyEngine, PolicyRequest
from ekassistant.runs.aggregate import Run
from ekassistant.runs.machine import is_terminal


class CapabilityDenied(Exception):
    """Authorization refused. Carries the reason verbatim from whichever
    check failed, so a caller logging this does not have to re-derive it.
    """

    def __init__(self, reason: str, *, contract_ref: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.contract_ref = contract_ref


class CapabilityExecutionError(Exception):
    """The capability was authorized and then failed.

    Carries the charged `run` because the budget was already spent and the
    caller must not lose that - a failed attempt that costs nothing is a
    free retry loop.

    For an IRREVERSIBLE_EXTERNAL capability this exception means the
    outcome is *unknown*, not that nothing happened. The gateway
    deliberately does not mark the effect intent FAILED in that case: that
    would assert the send did not occur, which nothing at this layer
    knows. The intent is left unsettled and reconciliation is owed.
    """

    def __init__(self, reason: str, *, run: Run, contract_ref: str, cause: Exception | None = None):
        super().__init__(reason)
        self.reason = reason
        self.run = run
        self.contract_ref = contract_ref
        self.__cause__ = cause


@dataclass(frozen=True)
class InvocationOutcome:
    """The result of an invocation attempt, allowed or not.

    Returns rather than raises on denial, because the Run is charged for
    the attempt either way and an exception path makes it far too easy to
    drop the charged copy. `unwrap()` is there for call sites that
    genuinely want exception semantics and have already taken `run`.
    """

    run: Run
    allowed: bool
    reason: str
    policy_version: str
    contract_ref: str | None = None
    result: ToolResult | None = None

    def unwrap(self) -> ToolResult:
        if not self.allowed or self.result is None:
            raise CapabilityDenied(self.reason, contract_ref=self.contract_ref)
        return self.result


class CapabilityGateway:
    def __init__(
        self,
        registry: CapabilityRegistry,
        policy: PolicyEngine,
        ledger: EffectLedger,
        audit: AuditSink,
    ):
        self._registry = registry
        self._policy = policy
        self._ledger = ledger
        self._audit = audit

    def visible_contracts(self, run: Run) -> list[ToolContract]:
        """Exactly the tools this Run may be offered.

        This is what a planner or a model-facing tool list is built from -
        never `registry.all_contracts()`. See registry.visible_to().
        """
        return self._registry.visible_to(zones=run.allowed_zones, scopes=run.approved_scopes)

    def canonical_payload(
        self, tool: str, payload: Mapping[str, Any], version: int | None = None
    ) -> dict[str, Any]:
        """Normalize a payload exactly as `invoke` will.

        Public because approval flows need it. The gateway hashes the
        *validated* payload, so anything that computes a hash separately -
        proposing an intent, showing a reviewer what they are approving -
        has to normalize identically or the hashes silently disagree and
        every approval fails with a mismatch that looks like tampering.
        One shared normalizer removes that class of bug rather than
        documenting around it.
        """
        contract = self._registry.contract(tool, version)
        return contract.input_model.model_validate(dict(payload)).model_dump(mode="json")

    def propose_effect(
        self,
        run: Run,
        tool: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        version: int | None = None,
        payload_summary: Mapping[str, Any] | None = None,
    ) -> EffectIntent:
        """Record an effect intent for a payload, before invoking it.

        Goes through the gateway rather than straight to the ledger so the
        payload is canonicalized once, by the same code path that will
        validate it at invoke time.

        Deliberately does *not* check authority: proposing is not doing,
        and an intent for something the Run cannot invoke is refused at
        `invoke()` with the reason recorded. Refusing here instead would
        lose the audit record of the attempt.
        """
        contract = self._registry.contract(tool, version)
        if contract.effect_class not in REQUIRES_EFFECT_INTENT:
            raise CapabilityDenied(
                f"{contract.ref} is {contract.effect_class} and needs no effect intent",
                contract_ref=contract.ref,
            )
        return self._ledger.propose(
            run.run_id,
            contract,
            self.canonical_payload(tool, payload, version),
            idempotency_key,
            payload_summary,
        )

    def invoke(
        self,
        run: Run,
        tool: str,
        payload: Mapping[str, Any],
        *,
        version: int | None = None,
        effect_intent_id: str | None = None,
    ) -> InvocationOutcome:
        started = time.monotonic()

        if is_terminal(run):
            return self._denied(run, f"run {run.run_id} is terminal ({run.status})", None, started)

        # Budget is checked before anything else and charged immediately
        # after, so that even denied attempts consume a step. A model
        # looping on a forbidden tool must run out of budget rather than
        # loop for free.
        exhausted = run.would_exhaust(steps=1)
        if exhausted:
            return self._denied(run, f"budget exhausted: {exhausted}", None, started)
        run = run.charge(steps=1)

        try:
            capability = self._registry.get(tool, version)
        except CapabilityNotFound:
            requested = tool if version is None else f"{tool}@{version}"
            return self._denied(run, f"unknown capability {requested!r}", None, started)

        contract = capability.contract

        denial = self._check_authority(run, contract)
        if denial:
            return self._denied(run, denial, contract, started)

        decision = self._policy.evaluate(
            PolicyRequest(
                tenant_id=run.tenant_id,
                principal_id=run.principal_id,
                purpose=run.purpose,
                task_type=run.task_type,
                granted_scopes=run.approved_scopes,
                contract=contract,
            )
        )
        if not decision.allowed:
            return self._denied(run, decision.reason, contract, started)

        try:
            validated = contract.input_model.model_validate(dict(payload))
        except ValidationError as exc:
            return self._denied(
                run, f"payload does not satisfy {contract.ref} input schema: {exc}", contract,
                started,
            )
        # Hash and pass on the *validated* payload, not the raw input.
        # Pydantic coerces, so the raw dict and what the tool actually
        # receives can differ - binding an approval to the raw form would
        # approve something other than what executes.
        effective_payload = validated.model_dump(mode="json")
        payload_hash = canonical_hash(effective_payload)

        intent = None
        if contract.effect_class in REQUIRES_EFFECT_INTENT:
            intent, denial = self._resolve_intent(
                run, contract, effective_payload, effect_intent_id
            )
            if denial:
                return self._denied(run, denial, contract, started, payload_hash=payload_hash)
        elif effect_intent_id is not None:
            return self._denied(
                run,
                f"{contract.ref} is {contract.effect_class} and takes no effect intent",
                contract,
                started,
                payload_hash=payload_hash,
            )

        ctx = InvocationContext(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            principal_id=run.principal_id,
            purpose=run.purpose,
            granted_scopes=run.approved_scopes,
            limits=contract.limits,
            idempotency_key=intent.idempotency_key if intent else None,
            deadline_ts=run.budget.deadline_ts,
        )

        try:
            result = capability.invoke(effective_payload, ctx)
            contract.output_model.model_validate(dict(result.data))
        except Exception as exc:
            self._record(
                run, contract, allowed=True,
                reason=f"capability raised {type(exc).__name__}: {exc}",
                policy_version=decision.policy_version, started=started,
                payload_hash=payload_hash,
                effect_intent_id=intent.intent_id if intent else None,
            )
            raise CapabilityExecutionError(
                f"{contract.ref} failed: {exc}", run=run, contract_ref=contract.ref, cause=exc
            ) from exc

        if intent is not None:
            self._ledger.mark_committed(intent.intent_id, result.provenance.source_ref)
            run = run.with_effect_intent(intent.intent_id)

        self._record(
            run, contract, allowed=True, reason=decision.reason,
            policy_version=decision.policy_version, started=started,
            payload_hash=payload_hash, result_hash=canonical_hash(result.data),
            effect_intent_id=intent.intent_id if intent else None,
        )

        return InvocationOutcome(
            run=run,
            allowed=True,
            reason=decision.reason,
            policy_version=decision.policy_version,
            contract_ref=contract.ref,
            result=result,
        )

    # -- checks ------------------------------------------------------

    @staticmethod
    def _check_authority(run: Run, contract: ToolContract) -> str | None:
        """The three things the Run itself must already carry. Returns a
        denial reason, or None to continue.
        """
        if not run.permits_zone(contract.zone):
            return (
                f"{contract.ref} runs in zone {contract.zone}, which this run does not carry "
                f"({sorted(run.allowed_zones)})"
            )
        missing = contract.required_scopes - run.approved_scopes
        if missing:
            return f"run lacks scopes {sorted(missing)} required by {contract.ref}"
        if not run.permits_classification(contract.max_classification):
            return (
                f"{contract.ref} handles {contract.max_classification.name} data, above this "
                f"run's ceiling of {run.classification_ceiling.name}"
            )
        return None

    def _resolve_intent(self, run, contract, payload, effect_intent_id):
        """Validate the effect intent backing a write. Returns
        (intent, denial_reason) with exactly one of the two set.
        """
        if effect_intent_id is None:
            return None, (
                f"{contract.ref} is {contract.effect_class} and requires a recorded "
                f"effect intent before it can be invoked"
            )
        try:
            intent = self._ledger.get(effect_intent_id)
        except EffectNotFound:
            return None, f"unknown effect intent {effect_intent_id!r}"

        if intent.run_id != run.run_id:
            return None, f"effect intent {effect_intent_id!r} belongs to another run"
        if intent.contract_ref != contract.ref:
            return None, (
                f"effect intent {effect_intent_id!r} was proposed for {intent.contract_ref}, "
                f"not {contract.ref}"
            )

        ok, reason = authorized_for(
            intent, payload, approval_required=contract.effect_class in REQUIRES_APPROVAL
        )
        if not ok:
            return None, reason

        # Duplicate suppression across intents: a second intent carrying
        # an idempotency key that already committed is the retried-send
        # case, and must not execute again.
        duplicate = self._ledger.find_committed(intent.idempotency_key)
        if duplicate is not None and duplicate.intent_id != intent.intent_id:
            return None, (
                f"idempotency key already committed as {duplicate.intent_id} "
                f"(provider ref {duplicate.provider_ref})"
            )

        return intent, None

    # -- audit -------------------------------------------------------

    def _denied(
        self,
        run: Run,
        reason: str,
        contract: ToolContract | None,
        started: float,
        payload_hash: str | None = None,
    ) -> InvocationOutcome:
        self._record(
            run, contract, allowed=False, reason=reason,
            policy_version=self._policy.policy_version, started=started,
            payload_hash=payload_hash,
        )
        return InvocationOutcome(
            run=run,
            allowed=False,
            reason=reason,
            policy_version=self._policy.policy_version,
            contract_ref=contract.ref if contract else None,
        )

    def _record(
        self,
        run: Run,
        contract: ToolContract | None,
        *,
        allowed: bool,
        reason: str,
        policy_version: str,
        started: float,
        payload_hash: str | None = None,
        result_hash: str | None = None,
        effect_intent_id: str | None = None,
    ) -> None:
        self._audit.record(
            InvocationRecord(
                run_id=run.run_id,
                tenant_id=run.tenant_id,
                principal_id=run.principal_id,
                purpose=run.purpose,
                task_type=run.task_type,
                contract_ref=contract.ref if contract else None,
                effect_class=contract.effect_class if contract else None,
                zone=contract.zone if contract else None,
                allowed=allowed,
                reason=reason,
                policy_version=policy_version,
                payload_hash=payload_hash,
                result_hash=result_hash,
                effect_intent_id=effect_intent_id,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        )
