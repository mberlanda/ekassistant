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

from ekassistant.ingest.parsers._shared import escape_accidental_heading


def parse(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = [_escape_lines(page.extract_text()) for page in reader.pages]
    return "\n\n".join(page.strip() for page in pages if page.strip())


def _escape_lines(text: str) -> str:
    # PDF body text has no Markdown heading semantics - a line that
    # happens to start with "# " (e.g. "#1234: ..." wrapping onto its own
    # line, a pasted heading-style note) must not be mistaken for a real
    # heading by chunker.py's _HEADING_RE, since chunker.py's heading
    # detection operates per line, not per page.
    return "\n".join(escape_accidental_heading(line) for line in text.splitlines())
