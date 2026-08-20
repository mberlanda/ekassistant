"""Workflow template lookup, keyed by task type and version.

See docs/design/runs.md#workflows.

Separate from the capability registry on purpose. A capability is
something the system *can do*; a workflow is something it *knows how to
do end to end*, including which capabilities it needs and what
transitions it drives. Conflating them would put "send an email" and
"draft, validate, get approval for, and send a client update" at the same
level of abstraction, which is exactly the collapse ADR-0013 warns about.
"""

from ekassistant.workflows.base import Workflow


class WorkflowNotFound(KeyError):
    pass


class DuplicateWorkflow(Exception):
    pass


class WorkflowRegistry:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int], Workflow] = {}
        self._versions: dict[str, list[int]] = {}

    def register(self, workflow: Workflow) -> None:
        key = (workflow.task_type, workflow.version)
        if key in self._by_key:
            raise DuplicateWorkflow(f"{workflow.task_type} v{workflow.version} already registered")
        self._by_key[key] = workflow
        self._versions.setdefault(workflow.task_type, []).append(workflow.version)
        self._versions[workflow.task_type].sort()

    def get(self, task_type: str, version: int | None = None) -> Workflow:
        versions = self._versions.get(task_type)
        if not versions:
            raise WorkflowNotFound(task_type)
        resolved = versions[-1] if version is None else version
        try:
            return self._by_key[(task_type, resolved)]
        except KeyError as exc:
            raise WorkflowNotFound(f"{task_type} v{resolved}") from exc

    def task_types(self) -> list[str]:
        return sorted(self._versions)
