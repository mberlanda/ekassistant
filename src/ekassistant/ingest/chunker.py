"""Structure-aware Markdown chunker.

See docs/design/ingest.md#tradeoffs: splits on heading boundaries first so
each chunk is one coherent, citable section, rather than a fixed-size
window that can cut a citable unit in half. A section that's still too
long past that point is further split on paragraph boundaries, greedily
packed up to _MAX_CHARS.
"""

import re

from ekassistant.index.types import IndexedChunk

_HEADING_RE = re.compile(r"^#{1,6}\s+.*$")
# Generous relative to nomic-embed-text's 8192-token context window (see
# ADR-0006) - this bound exists to keep each chunk focused on one idea for
# citation precision, not because the embedder would truncate otherwise.
_MAX_CHARS = 800


def chunk_document(
    doc_id: str, source: str, text: str, allowed_groups: list[str]
) -> list[IndexedChunk]:
    chunks = []
    index = 0
    for section in _split_by_heading(text):
        for piece in _split_by_paragraph_if_too_long(section):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                IndexedChunk(
                    chunk_id=f"{doc_id}#{index}",
                    source=source,
                    text=piece,
                    allowed_groups=allowed_groups,
                )
            )
            index += 1
    return chunks


def _split_by_heading(text: str) -> list[str]:
    lines = text.splitlines()
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if _HEADING_RE.match(line) and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return [s for s in sections if s]


def _split_by_paragraph_if_too_long(section: str) -> list[str]:
    if len(section) <= _MAX_CHARS:
        return [section]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > _MAX_CHARS and current:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces
