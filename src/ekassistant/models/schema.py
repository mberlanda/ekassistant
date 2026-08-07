"""Structured cite-or-abstain output contract.

See docs/design/model-layer.md#cite-or-abstain-contract.
"""

from pydantic import BaseModel


class Citation(BaseModel):
    chunk_id: str
    source: str


class GroundedAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    abstained: bool
