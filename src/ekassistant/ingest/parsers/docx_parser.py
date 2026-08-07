"""DOCX parser: paragraph style ("Heading N") -> Markdown-ish `#` text.

Unlike PDF, DOCX carries real semantic heading structure via paragraph
styles, so this parser gets the same heading-aware chunking PDF can't.
"""

from pathlib import Path

import docx


def parse(path: Path) -> str:
    document = docx.Document(str(path))
    lines = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = paragraph.style.name if paragraph.style else ""
        if style_name.startswith("Heading "):
            level = _heading_level(style_name)
            lines.append(f"{'#' * level} {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)


def _heading_level(style_name: str) -> int:
    # "Heading 1" -> 1, "Heading 2" -> 2, ... clamped to Markdown's max of 6.
    suffix = style_name.removeprefix("Heading ").strip()
    level = int(suffix) if suffix.isdigit() else 1
    return min(max(level, 1), 6)
