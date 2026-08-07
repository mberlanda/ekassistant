"""Local filesystem connector.

See docs/design/ingest.md: each connector implements one interface so
chunking, embedding, and index writing are shared code across every
source. This one reads a manifest-described set of local files, dispatching
each to its format-specific parser (see ekassistant.ingest.parsers) by file
extension - first used for the seed corpus (docs/roadmap.md item 3),
reusable later for a real share-drive-style source.
"""

from pathlib import Path

import yaml

from ekassistant.ingest.connectors.document import Document, validate_allowed_groups
from ekassistant.ingest.parsers import parse_document

__all__ = ["Document", "FilesystemConnector"]


class FilesystemConnector:
    def __init__(self, corpus_dir: Path, manifest_path: Path):
        self._corpus_dir = corpus_dir
        self._manifest_path = manifest_path

    def load_documents(self) -> list[Document]:
        manifest = yaml.safe_load(self._manifest_path.read_text()) or {}
        documents = []
        for filename, meta in manifest.items():
            text = parse_document(self._corpus_dir / filename)
            documents.append(
                Document(
                    doc_id=filename,
                    source=filename,
                    text=text,
                    # Missing/absent allowed_groups fails closed (visible to
                    # no one), never treated as public - see ADR-0002.
                    allowed_groups=validate_allowed_groups(filename, meta.get("allowed_groups")),
                )
            )
        return documents
