"""Structured cite-or-abstain output contract.

See docs/design/model-layer.md#cite-or-abstain-contract.
"""

from pydantic import BaseModel


class Citation(BaseModel):
    chunk_id: str
    source: str


class ModelResponse(BaseModel):
    """The raw structured-output contract sent to, and parsed from, the
    LLM (via chat_json's json_schema argument). Deliberately separate
    from GroundedAnswer below: the model must never be asked to produce
    (or constrained by) `reason`, which is populated by our own
    validation logic in generation.py, not the model.
    """

    answer: str
    citations: list[Citation]
    abstained: bool


class GroundedAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    abstained: bool
    reason: str | None = None
    """Why this is an abstain (see generation.py's REASON_* constants),
    or None when abstained is False. Exists so "abstain rate" and
    "citation-validation failure rate" - two distinct metrics named in
    docs/design/observability.md - are actually distinguishable from a
    GroundedAnswer/RunTrace, instead of collapsing into one boolean.
    """
