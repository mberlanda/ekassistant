"""Shared embedding port.

Both ingest (embedding chunks at write time) and retrieval (embedding the
query at read time) only need "something that turns text into a vector" -
this Protocol is the one shared definition of that shape, implemented
concretely by OllamaClient (ollama_client.py) and satisfied structurally
by any fake used in tests.
"""

from typing import Protocol


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...
