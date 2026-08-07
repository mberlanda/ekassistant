"""PDF parser: page text extraction, no heading detection.

Unlike HTML/DOCX, plain PDF text extraction exposes no reliable semantic
structure (font size differences aren't a heading signal pypdf's basic
extract_text() surfaces) - so, deliberately, no `#` markers are
synthesized here. The chunker's heading-based splitting is a no-op on
this output; it falls through to paragraph/character-budget splitting
instead. See docs/decisions/0009-ingest-format-adapters.md.
"""

from pathlib import Path

from pypdf import PdfReader


def parse(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = [page.extract_text() for page in reader.pages]
    return "\n\n".join(page.strip() for page in pages if page.strip())
