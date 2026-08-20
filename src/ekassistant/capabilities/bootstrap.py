"""Building a registry from configuration - the extension point.

See docs/design/capabilities.md#adding-a-capability.

`CATALOGUE` maps a tool name to the factory that builds it. Adding a real
capability means writing the class, adding one line here, and enabling it
in `config/capabilities.yaml` - nothing in the gateway, the policy engine
or any workflow changes, because they all address capabilities through
the contract, never through the implementation.

The split between catalogue and config is what makes the same build
deployable with different tool sets: a research-only deployment enables
`web_search` and nothing in the EFFECT zone, and that is a config change
rather than a code path.
"""

from collections.abc import Callable, Iterable
from pathlib import Path

import yaml

from ekassistant.capabilities.contracts import Capability
from ekassistant.capabilities.mocks.crm import MockCrmAccountLookup
from ekassistant.capabilities.mocks.email import MockDraftStore, MockEmailSend
from ekassistant.capabilities.mocks.metrics import MockCertifiedMetric
from ekassistant.capabilities.mocks.web import MockWebSearch
from ekassistant.capabilities.registry import CapabilityRegistry

#: Factories, not instances. Several capabilities hold state (the draft
#: store, the outbox), and a module-level instance would leak that state
#: between two registries - which in tests reads as one test's send
#: appearing in another's outbox.
CATALOGUE: dict[str, Callable[[], Capability]] = {
    "crm_account_lookup": MockCrmAccountLookup,
    "certified_metric": MockCertifiedMetric,
    "web_search": MockWebSearch,
    "save_email_draft": MockDraftStore,
    "send_email": MockEmailSend,
}


class UnknownCapability(Exception):
    """Raised when config enables a name the catalogue does not have.

    Loud at load time rather than skipped, for the reason PolicyConfigError
    gives: a typo would otherwise silently produce a deployment missing a
    tool, and "the assistant can't do that any more" is a much harder
    thing to trace back to a config file than a failure to start.
    """


def build_registry(enabled: Iterable[str] | None = None) -> CapabilityRegistry:
    """Build a registry containing the named capabilities.

    `enabled=None` means the whole catalogue - convenient for tests, and
    deliberately not the behaviour of the config path below, where an
    absent `enabled:` list means an empty registry rather than everything.
    """
    names = list(CATALOGUE) if enabled is None else list(enabled)

    registry = CapabilityRegistry()
    for name in names:
        factory = CATALOGUE.get(name)
        if factory is None:
            raise UnknownCapability(
                f"{name!r} is not in the capability catalogue; known: {sorted(CATALOGUE)}"
            )
        registry.register(factory())
    return registry


def registry_from_yaml(path: Path) -> CapabilityRegistry:
    """Build a registry from config/capabilities.yaml.

    A missing or empty `enabled:` list yields an *empty* registry, not the
    full catalogue. Fail-closed: an unreadable or half-written config
    should leave the system able to do nothing, never able to do
    everything.
    """
    raw = yaml.safe_load(path.read_text()) or {}
    return build_registry(raw.get("enabled") or [])
