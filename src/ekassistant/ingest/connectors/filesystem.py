"""Local filesystem connector.

See docs/design/ingest.md: each connector implements one interface so
parsing, chunking, embedding, and index writing are shared code across
every source. This one reads a manifest-described set of local files -
first used for the seed corpus (docs/roadmap.md item 3), reusable later
for a real share-drive-style source.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Document:
    doc_id: str
    source: str
    text: str
    allowed_groups: list[str]


class FilesystemConnector:
    def __init__(self, corpus_dir: Path, manifest_path: Path):
        self._corpus_dir = corpus_dir
        self._manifest_path = manifest_path

    def load_documents(self) -> list[Document]:
        manifest = yaml.safe_load(self._manifest_path.read_text()) or {}
        documents = []
        for filename, meta in manifest.items():
            text = (self._corpus_dir / filename).read_text()
            documents.append(
                Document(
                    doc_id=filename,
                    source=filename,
                    text=text,
                    # Missing/absent allowed_groups fails closed (visible to
                    # no one), never treated as public - see ADR-0002.
                    allowed_groups=_validated_allowed_groups(filename, meta.get("allowed_groups")),
                )
            )
        return documents


def _validated_allowed_groups(filename: str, allowed_groups: object) -> list[str]:
    """Rejects, rather than silently accepts, a manifest typo like
    `allowed_groups: engineering` (a bare string, missing the [] brackets)
    in place of `allowed_groups: [engineering]`. Left unvalidated, a bare
    string would be iterated character-by-character wherever downstream
    code does `for group in chunk.allowed_groups` (e.g. the keyword
    index's junction-table writes), producing bogus single-character
    "groups" - while a store that stores/matches the field as a whole
    value (the vector index's payload) could still treat it as one real
    group. That divergence between what the two indexes end up enforcing
    is exactly the vector/keyword drift ADR-0005 flags as highest-priority
    to avoid, so this fails loudly here instead.
    """
    if allowed_groups is None:
        return []
    if not isinstance(allowed_groups, list) or not all(
        isinstance(group, str) for group in allowed_groups
    ):
        raise ValueError(
            f"{filename}: allowed_groups must be a list of strings, got {allowed_groups!r}"
        )
    return allowed_groups
