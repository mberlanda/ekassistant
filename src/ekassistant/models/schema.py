"""Structured cite-or-abstain output contract.

See docs/design/model-layer.md#cite-or-abstain-contract.
"""

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """A citation as surfaced to callers (API/TUI/traces), carrying the
    real chunk_id. Always rebuilt from the authoritative context chunk in
    generation.py - never deserialized directly from model output, which
    uses ModelCitation's surrogate index instead.
    """

    chunk_id: str
    source: str


# WHY AN INDEX AND NOT A chunk_id (kept as a comment, NOT a docstring:
# pydantic copies a model's docstring into its JSON schema `description`,
# and that schema is handed straight to the LLM via chat_json's `format` -
# so anything written below would become prompt text the model has to read.
# Measured: a long rationale here made granite4.1:8b stop emitting
# citations entirely.)
#
# The model used to be asked for `chunk_id` + `source` verbatim. That
# contract was ambiguous whenever a chunk_id was itself composite - the web
# crawler produces `<url>#<n>` (see ingest/chunker.py) - because a
# reasonable model reads two fields, sees `url#n`, and splits it along the
# obvious seam: chunk_id="7", source="https://...". Observed live with
# granite4.1:8b, which produced correct, well-grounded answers that were
# then thrown away by citation validation. llama3.2:1b only passed because
# it echoed the opaque string verbatim rather than interpreting it - i.e.
# the contract was latently broken and merely masked by one model's
# literal-mindedness, not validated by it.
#
# A bare integer has no internal structure to split, is far cheaper to emit
# than a ~70-character URL, and (being grammar-constrained to a number)
# cannot carry a fabricated source label at all. `source` is deliberately
# NOT requested: it is always rebuilt from the authoritative context chunk,
# so asking for it would only re-introduce the ambiguity for a value that
# gets discarded.
class ModelCitation(BaseModel):
    """One cited context chunk, referenced by its bracketed number."""

    index: int = Field(
        ge=1,
        description=(
            "The number shown in square brackets before a context chunk, "
            "e.g. 3 for a chunk introduced by '[3]'. Use the bracketed "
            "number exactly as shown. Never use a URL, filename, or any "
            "identifier appearing inside the chunk's text or source label."
        ),
    )


class ModelResponse(BaseModel):
    """The raw structured-output contract sent to, and parsed from, the
    LLM (via chat_json's json_schema argument). Deliberately separate
    from GroundedAnswer below: the model must never be asked to produce
    (or constrained by) `reason`, which is populated by our own
    validation logic in generation.py, not the model.
    """

    answer: str
    citations: list[ModelCitation]
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
