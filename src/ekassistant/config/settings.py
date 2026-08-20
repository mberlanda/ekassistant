"""Environment-driven settings.

Every value here is documented in .env.example alongside the ADR that
motivated its default. Nothing is hardcoded elsewhere in the codebase.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Model layer (see docs/decisions/0004, docs/decisions/0006)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:1b"
    ollama_embed_model: str = "nomic-embed-text"
    # Default generation temperature (see models/generation.py's
    # DEFAULT_TEMPERATURE, which duplicates this value and explains why
    # it matches Ollama's own stock default rather than a lower value -
    # a lower default was tried and measured worse in repeated live
    # testing, not just left at the stock value out of caution).
    # Overridable per-request (see QueryRequest.temperature / the TUI's
    # `:temp` command).
    ollama_temperature: float = 0.8

    # Vector index (see docs/decisions/0005). embedding_dimensions must match
    # whatever ollama_embed_model actually outputs (768 for nomic-embed-text,
    # see docs/decisions/0006) - changing the embedding model means changing
    # this too, and recreating the collection at the new dimensionality.
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "ekassistant_chunks"
    embedding_dimensions: int = 768

    # Keyword index (see docs/decisions/0005)
    keyword_index_path: Path = Path("./data/keyword_index.sqlite3")

    # Mock identity (see docs/decisions/0007)
    identities_path: Path = Path("./config/identities.yaml")
    default_user: str = "guest"

    # Ingest (see docs/design/ingest.md). The seed corpus is a tiny
    # hand-written fixture set for the end-to-end proof of concept, not a
    # real source connector - see roadmap.md item 3.
    seed_corpus_dir: Path = Path("./seed_corpus")
    seed_corpus_manifest: Path = Path("./seed_corpus/manifest.yaml")

    # Web crawler connector (see docs/decisions/0010). Empty by default -
    # no domain is baked into the code, see ADR-0010.
    crawl_targets_path: Path = Path("./config/crawl_targets.yaml")

    # Observability (see docs/design/observability.md). Local, file-based
    # for V1 - no tracing backend.
    trace_log_path: Path = Path("./data/traces.jsonl")

    # Agent capabilities and policy (see docs/design/capabilities.md and
    # docs/design/policy.md). Both files are mocks in the same sense
    # identities.yaml is (ADR-0007) - real vocabulary, static backing
    # store. capabilities.yaml ships with every mock enabled; narrowing it
    # is the supported way to run a deployment that, say, can research the
    # web but has no ability to send anything.
    policies_path: Path = Path("./config/policies.yaml")
    capabilities_path: Path = Path("./config/capabilities.yaml")

    # Kept separate from trace_log_path on purpose: an audit record answers
    # "what was this authorized to do", a trace answers "how did it
    # behave", and they have different readers and different retention.
    # See docs/design/capabilities.md#audit.
    audit_log_path: Path = Path("./data/capability_audit.jsonl")

    # Durable Run store (see docs/design/runs.md#persistence, Phase 1).
    # SqliteRunStore opens a fresh connection per call rather than holding
    # one for the object's lifetime (contrast SqliteKeywordIndex), so this
    # path is safe to share across a request pool the way keyword_index_path
    # is not - see runs/sqlite_store.py's module docstring.
    runs_db_path: Path = Path("./data/runs.sqlite3")


@lru_cache
def get_settings() -> Settings:
    return Settings()
