"""HTML parser: heading and paragraph tags -> Markdown-ish `#` text.

Only <h1>-<h6> and <p> are read, in document order; everything else
(nav, script, style, tables, lists, ...) is dropped. That's a deliberate
scope limit for a "core format adapters" PR, not full HTML-to-Markdown
conversion - see docs/decisions/0009-ingest-format-adapters.md.
"""

from pathlib import Path

from bs4 import BeautifulSoup

from ekassistant.ingest.parsers._shared import escape_accidental_heading

_HEADING_TAGS = [f"h{level}" for level in range(1, 7)]


def parse(path: Path) -> str:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    lines = []
    for tag in soup.find_all([*_HEADING_TAGS, "p"]):
        # get_text(strip=True) with no separator concatenates adjacent
        # inline tags' text with nothing between them (e.g. "Contact
        # <a>the on-call engineer</a>immediately." -> "Contactthe
        # on-call engineerimmediately." with no separator vs. the
        # correctly spaced text a " " separator produces) - a real risk
        # for <p> tags containing links/emphasis, which is exactly the
        # kind of content real wiki/runbook HTML has.
        text = " ".join(tag.get_text(" ", strip=True).split())
        if not text:
            continue
        if tag.name in _HEADING_TAGS:
            level = int(tag.name[1])
            lines.append(f"{'#' * level} {text}")
        else:
            # <p> text has no Markdown heading semantics of its own - if it
            # happens to start with "# " (a pasted heading-style line, a
            # ticket reference), it must not be mistaken for a real
            # heading by chunker.py's _HEADING_RE.
            lines.append(escape_accidental_heading(text))
    return "\n\n".join(lines)
