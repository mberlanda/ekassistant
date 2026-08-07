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


@lru_cache
def get_settings() -> Settings:
    return Settings()
