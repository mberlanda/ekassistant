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
                    allowed_groups=meta.get("allowed_groups", []),
                )
            )
        return documents
