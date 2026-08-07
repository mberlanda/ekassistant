"""Context chunks handed to the model layer for grounding.

See docs/design/retrieval.md#assemble-context (the retrieval pipeline
produces these; the model layer only consumes them).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextChunk:
    chunk_id: str
    source: str
    text: str
