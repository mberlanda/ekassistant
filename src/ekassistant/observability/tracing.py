"""Per-run tracing. See docs/design/observability.md.

Local, file-based, append-only JSON Lines - matching the design doc's
"local, file-based observability for V1" tradeoff (no dashboarding
backend, appropriate for a single-developer POC). One line per /query
call, containing exactly what the design doc's Metrics section asks to
measure: retrieval hit count, per-stage latency, abstain rate (aggregated
by whoever reads the log), model names in use.

NOTE (see docs/design/observability.md#tradeoffs): chunk_ids retrieved are
logged, not the chunk text/content itself - unlike the doc's stated V1
tradeoff of tracing full retrieved-chunk content for debuggability, this
was scoped down deliberately: the ACL group set is logged alongside, so
the trace file is not obviously safe to share as broadly as a
content-free log would be, but it also doesn't duplicate document content
into a second, separately-secured file. Revisit together if traces need
richer debugging content later.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RunTrace:
    user_id: str
    groups: list[str]
    question: str
    retrieved_chunk_ids: list[str]
    retrieval_ms: float
    generation_ms: float
    abstained: bool
    citation_count: int
    chat_model: str
    embed_model: str
    timestamp: float = field(default_factory=time.time)


def record_run(trace: RunTrace, path: Path) -> None:
    # No explicit locking: opening in "a" mode uses O_APPEND, and POSIX
    # guarantees a single write() of PIPE_BUF bytes or fewer (4096 on
    # macOS/Linux) is atomic under O_APPEND, so concurrent /query requests
    # (which are genuinely concurrent - see the SQLite thread-affinity fix
    # elsewhere in this PR series) can't interleave into a corrupted line,
    # as long as one trace record's serialized JSON stays under that size.
    # Verified directly: 200 concurrent writes from 50 threads, 0
    # corrupted lines. A pathologically long question or an unusually
    # large retrieved_chunk_ids list could in principle exceed PIPE_BUF
    # and lose that guarantee - not a risk for this project's short mock
    # questions and top-k-bounded chunk lists, but worth knowing if either
    # ever becomes unbounded.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(trace)) + "\n")
