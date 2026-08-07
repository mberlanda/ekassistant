"""HTML parser: heading and paragraph tags -> Markdown-ish `#` text.

Only <h1>-<h6> and <p> are read, in document order; everything else
(nav, script, style, tables, lists, ...) is dropped. That's a deliberate
scope limit for a "core format adapters" PR, not full HTML-to-Markdown
conversion - see docs/decisions/0009-ingest-format-adapters.md.
"""

from pathlib import Path

from bs4 import BeautifulSoup

_HEADING_TAGS = [f"h{level}" for level in range(1, 7)]


def parse(path: Path) -> str:
    soup = BeautifulSoup(path.read_text(), "html.parser")
    lines = []
    for tag in soup.find_all([*_HEADING_TAGS, "p"]):
        text = tag.get_text(strip=True)
        if not text:
            continue
        if tag.name in _HEADING_TAGS:
            level = int(tag.name[1])
            lines.append(f"{'#' * level} {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)
