"""Per-format parsers: normalize a source file into Markdown-ish text.

See docs/design/ingest.md and docs/decisions/0009-ingest-format-adapters.md.
Every parser here produces text using the same convention chunker.py
already understands: `#`-prefixed heading lines where the source format
has real structure, plain paragraphs otherwise - so one chunker serves
every format instead of each parser needing its own chunking logic.
"""

from pathlib import Path

from ekassistant.ingest.parsers import docx_parser, html_parser, pdf_parser, text_parser

PARSERS = {
    ".txt": text_parser.parse,
    ".md": text_parser.parse,
    ".html": html_parser.parse,
    ".htm": html_parser.parse,
    ".pdf": pdf_parser.parse,
    ".docx": docx_parser.parse,
}


def parse_document(path: Path) -> str:
    try:
        parser = PARSERS[path.suffix.lower()]
    except KeyError:
        raise ValueError(
            f"{path.name}: unsupported file extension {path.suffix!r} "
            f"(supported: {sorted(PARSERS)})"
        ) from None
    return parser(path)
