"""CLI entry point: run the batch ingest pipeline against the seed corpus.

See docs/design/ingest.md and docs/roadmap.md item 3. Invoked via `make
ingest` / the `ekassistant-ingest` console script.
"""

from ekassistant.config.settings import get_settings
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.vector_index import QdrantVectorIndex
from ekassistant.ingest.connectors.filesystem import FilesystemConnector
from ekassistant.ingest.pipeline import run_ingest
from ekassistant.models.ollama_client import OllamaClient


def run() -> None:
    settings = get_settings()
    connector = FilesystemConnector(
        corpus_dir=settings.seed_corpus_dir,
        manifest_path=settings.seed_corpus_manifest,
    )
    embed_client = OllamaClient(settings)
    vector_index = QdrantVectorIndex(settings)
    vector_index.ensure_collection()
    keyword_index = SqliteKeywordIndex(settings)

    try:
        chunk_count = run_ingest(connector, embed_client, vector_index, keyword_index)
        print(f"Ingested {chunk_count} chunks from {settings.seed_corpus_dir}.")
    finally:
        keyword_index.close()


if __name__ == "__main__":
    run()
