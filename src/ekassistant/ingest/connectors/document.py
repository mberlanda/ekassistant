"""Shared connector output type.

Every connector (`FilesystemConnector`, `WebCrawlerConnector`, ...)
produces the same `Document` shape regardless of source, so `run_ingest`
(ingest/pipeline.py) never needs to know which connector produced it -
see docs/design/ingest.md#component-boundaries.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    doc_id: str
    source: str
    text: str
    allowed_groups: list[str]


def validate_allowed_groups(label: str, allowed_groups: object) -> list[str]:
    """Rejects, rather than silently accepts, a config typo like
    `allowed_groups: engineering` (a bare string, missing the [] brackets)
    in place of `allowed_groups: [engineering]`. Left unvalidated, a bare
    string would be iterated character-by-character wherever downstream
    code does `for group in chunk.allowed_groups` (e.g. the keyword
    index's junction-table writes), producing bogus single-character
    "groups" - while a store that stores/matches the field as a whole
    value (the vector index's payload) could still treat it as one real
    group. That divergence between what the two indexes end up enforcing
    is exactly the vector/keyword drift ADR-0005 flags as highest-priority
    to avoid, so this fails loudly here instead, shared by every connector
    that reads allowed_groups from YAML config.
    """
    if allowed_groups is None:
        return []
    if not isinstance(allowed_groups, list) or not all(
        isinstance(group, str) for group in allowed_groups
    ):
        raise ValueError(
            f"{label}: allowed_groups must be a list of strings, got {allowed_groups!r}"
        )
    return allowed_groups
