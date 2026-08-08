"""Structured cite-or-abstain output contract.

See docs/design/model-layer.md#cite-or-abstain-contract.
"""

from pydantic import BaseModel, Field


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
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "The model's own self-assessment of how well the provided "
            "context supports its answer (1.0 = fully supported, 0.0 = no "
            "support). Optional, not required, on purpose - live testing "
            "found that making this a 4th required field alongside answer/"
            "citations/abstained measurably pushed the small local model "
            "(see ADR-0004) into abstaining far more often, even at low "
            "temperature: reproduced at 0/6 successful answers on a "
            "previously reliable question with confidence required, vs. "
            "6/6 with it optional (the model still voluntarily included it "
            "on every one of those 6). Out-of-range values, when present, "
            "still fail schema validation like any other malformed field - "
            "see generation.py's REASON_MALFORMED_RESPONSE."
        ),
    )


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
    confidence: float | None = None
    """The model's raw, unvalidated self-reported confidence (see
    ModelResponse.confidence) - passed through as-is, never recalibrated
    or checked against anything, on the theory that even a poorly
    calibrated small model's confidence is a useful additional signal for
    a caller, not a claim this system verifies. Forced to None on every
    abstain path (including a citation-validation downgrade), matching
    `answer`/`citations` being wiped too: if the response wasn't trusted
    enough to surface as an answer, its self-reported confidence about
    that answer isn't trusted either.
    """
