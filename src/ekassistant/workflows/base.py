"""What a workflow is: an ordered set of steps over a Run.

See docs/design/runs.md#workflows and ADR-0014.

The shape encodes the orchestration choice. A workflow declares its steps
up front; it does not receive a planner that decides what to do next. A
model may fill structured fields inside a step and may choose among the
capabilities the Run already carries, but the sequence, the transitions,
the budgets and the effects belong to code.

That is a real constraint, not a formality: a template cannot handle a
request it was not written for, and the graduation path (ADR-0014) is to
measure where templates fail before adding agency, not to add agency in
case they might.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from ekassistant.capabilities.contracts import Zone
from ekassistant.capabilities.effects import EffectLedger
from ekassistant.capabilities.gateway import CapabilityGateway
from ekassistant.runs.aggregate import Run, RunStatus


@dataclass(frozen=True)
class WorkflowContext:
    """Everything a step is allowed to reach.

    Notably absent: settings, a database handle, an HTTP client, a model
    client of its own. A step acts through `gateway`, which means every
    external touch is a contract invocation that got authorized and
    audited. A step that needs something the gateway cannot provide needs
    a capability, not a shortcut.
    """

    run: Run
    gateway: CapabilityGateway
    ledger: EffectLedger


@dataclass(frozen=True)
class StepOutcome:
    """A step's result, including the Run it must be continued from.

    Returning the Run is what keeps the budget honest: the gateway charges
    the Run it was given and hands back a charged copy, and a step that
    forgot to propagate it would silently reset the spend. Making it the
    return value rather than a mutation means dropping it is a visible
    mistake at the call site.

    `request_status` lets a step ask for a transition it is not allowed to
    perform itself - a validator that finds an external recipient asks for
    AWAITING_APPROVAL, and the Run service decides whether that is legal
    from the current state.
    """

    run: Run
    ok: bool
    detail: str | None = None
    evidence_refs: tuple[str, ...] = ()
    request_status: RunStatus | None = None
    terminal_reason: str | None = None


@dataclass(frozen=True)
class StepSpec:
    """Declared metadata for one step, checked before a workflow runs."""

    name: str
    uses_contracts: tuple[str, ...] = field(default=())


class WorkflowStep(Protocol):
    spec: StepSpec

    def execute(self, ctx: WorkflowContext) -> StepOutcome: ...


class Workflow(Protocol):
    """A task template.

    `required_zones` and `required_scopes` are declared so a Run can be
    checked for sufficiency *before* execution starts, rather than failing
    on the third step because a scope was missing all along. A workflow
    that would be denied halfway is one that should never have been
    started - especially once a reversible write has already landed.
    """

    task_type: str
    version: int
    required_zones: frozenset[Zone]
    required_scopes: frozenset[str]

    def steps(self) -> Sequence[WorkflowStep]: ...


@dataclass(frozen=True)
class Sufficiency:
    ok: bool
    missing_scopes: frozenset[str] = frozenset()
    missing_zones: frozenset[Zone] = frozenset()

    @property
    def reason(self) -> str:
        parts = []
        if self.missing_scopes:
            parts.append(f"missing scopes {sorted(self.missing_scopes)}")
        if self.missing_zones:
            parts.append(f"missing zones {sorted(self.missing_zones)}")
        return "; ".join(parts) if parts else "run carries sufficient authority"


def check_sufficiency(run: Run, workflow: Workflow) -> Sufficiency:
    """Whether this Run carries enough authority to finish this workflow.

    Reports *all* shortfalls rather than the first, because the caller is
    usually a human being told why their request was refused, and
    discovering one missing scope at a time is a poor way to learn that
    the purpose was simply wrong.
    """
    missing_scopes = workflow.required_scopes - run.approved_scopes
    missing_zones = workflow.required_zones - run.allowed_zones
    return Sufficiency(
        ok=not missing_scopes and not missing_zones,
        missing_scopes=frozenset(missing_scopes),
        missing_zones=frozenset(missing_zones),
    )
