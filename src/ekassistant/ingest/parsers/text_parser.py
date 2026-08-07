"""Plain text / Markdown passthrough parser.

The file is already in the `#`-heading convention chunker.py expects (or
has no structure at all, which the chunker also handles), so there is
nothing to normalize.
"""

from pathlib import Path


def parse(path: Path) -> str:
    # Explicit encoding: read_text()'s default is locale-dependent, not
    # guaranteed UTF-8 across every environment this might run in.
    return path.read_text(encoding="utf-8")
