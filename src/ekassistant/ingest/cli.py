"""CLI entry point: run the batch ingest pipeline against every connector.

See docs/design/ingest.md and docs/roadmap.md items 3 and 5. Invoked via
`make ingest` / the `ekassistant-ingest` console script. Every connector
is combined via CompositeConnector into ONE run_ingest() call, not one
call per connector - see composite.py's docstring for why: run_ingest()'s
delete-propagation (docs/roadmap.md item 9) treats any source it didn't
see this run as removed and deletes it, so calling it once per connector
against these same shared indexes would make each later call's connector
"not seeing" the earlier connector's sources look like a mass removal.
This was caught live while wiring the crawler connector in - see
docs/decisions/0010-web-crawler-connector.md.
"""

from ekassistant.config.settings import get_settings
from ekassistant.index.keyword_index import SqliteKeywordIndex
from ekassistant.index.vector_index import QdrantVectorIndex
from ekassistant.ingest.connectors.composite import CompositeConnector
from ekassistant.ingest.connectors.crawler import WebCrawlerConnector
from ekassistant.ingest.connectors.filesystem import FilesystemConnector
from ekassistant.ingest.pipeline import run_ingest
from ekassistant.models.ollama_client import OllamaClient


def run() -> None:
    settings = get_settings()
    embed_client = OllamaClient(settings)
    vector_index = QdrantVectorIndex(settings)
    vector_index.ensure_collection()
    keyword_index = SqliteKeywordIndex(settings)
    crawler_connector = WebCrawlerConnector(config_path=settings.crawl_targets_path)

    try:
        filesystem_connector = FilesystemConnector(
            corpus_dir=settings.seed_corpus_dir,
            manifest_path=settings.seed_corpus_manifest,
        )
        connector = CompositeConnector([filesystem_connector, crawler_connector])

        chunk_count = run_ingest(connector, embed_client, vector_index, keyword_index)
        print(
            f"Ingested {chunk_count} chunks from {settings.seed_corpus_dir} "
            f"and {settings.crawl_targets_path}."
        )
        for url, reason in crawler_connector.skipped:
            print(f"  skipped {url}: {reason}")
    finally:
        keyword_index.close()
        crawler_connector.close()


if __name__ == "__main__":
    run()
