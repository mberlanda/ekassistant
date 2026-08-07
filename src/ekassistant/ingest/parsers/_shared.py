"""Shared helper for parsers that synthesize `#`-heading text.

Used by every parser except text_parser.py: a `.txt`/`.md` file's "#" is
the user's own, intentional Markdown heading, so it must pass through
unescaped. But html_parser.py/docx_parser.py/pdf_parser.py all emit body
text pulled from a source format that has no Markdown heading semantics
of its own - if that body text happens to start with "# " (a pasted
Markdown-style line, a numbered note like "# 1 priority item", a literal
"#1234: ..." ticket reference), it would be silently misread as a real
document heading by chunker.py's _HEADING_RE, fabricating a section
boundary out of ordinary prose. Escaping it here, at the one point each
parser controls precisely which lines are "real" headings, is cheaper and
more robust than trying to have the chunker guess the difference later.
"""

import re

# Mirrors chunker.py's _HEADING_RE (^#{1,6}\s+...) exactly: anything that
# would trip that regex must be escaped here.
_ACCIDENTAL_HEADING_RE = re.compile(r"^#{1,6}\s")


def escape_accidental_heading(text: str) -> str:
    if _ACCIDENTAL_HEADING_RE.match(text):
        return f"\\{text}"
    return text
