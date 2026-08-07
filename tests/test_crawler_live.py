"""Live crawler tests against a real local HTTP server.

Not mocked at the HTTP layer, and not pointed at a real third-party
website either - hitting the real internet from an automated test suite
would be flaky (network conditions, site changes) and imposes load on a
site this project doesn't control. A local `http.server` instance serving
real fixture files from disk is the equivalent "real infra" this project
otherwise insists on (real Qdrant, real Ollama - see docs/roadmap.md):
genuine HTTP requests/responses, genuine relative-URL resolution, genuine
robots.txt parsing, just against a server this test owns.
"""

import functools
import threading
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from ekassistant.ingest.connectors.crawler import WebCrawlerConnector
from ekassistant.ingest.pipeline import run_ingest

qdrant_client_module = pytest.importorskip("qdrant_client")

EMBEDDING = [0.1, 0.2, 0.3, 0.4]


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class ConstantEmbedder:
    def embed(self, text: str) -> list[float]:
        return EMBEDDING


def _qdrant_is_reachable(url: str) -> bool:
    try:
        qdrant_client_module.QdrantClient(url=url).get_collections()
    except Exception:
        return False
    return True


@pytest.fixture
def fixture_site(tmp_path):
    (tmp_path / "index.html").write_text(
        "<html><body>"
        "<h1>Index Page</h1>"
        "<p>Welcome to the local docs site.</p>"
        '<a href="/page2.html">Page 2</a>'
        '<a href="/private.html">Private</a>'
        "</body></html>"
    )
    (tmp_path / "page2.html").write_text(
        "<html><body><h1>Page Two</h1><p>Second page body content.</p></body></html>"
    )
    (tmp_path / "private.html").write_text(
        "<html><body><h1>Secret</h1><p>Should never be fetched.</p></body></html>"
    )
    (tmp_path / "robots.txt").write_text("User-agent: *\nDisallow: /private.html\n")

    handler = functools.partial(_QuietHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    yield f"http://127.0.0.1:{port}", tmp_path

    server.shutdown()
    server.server_close()


def test_crawls_real_local_server_following_links_and_respecting_robots_txt(fixture_site):
    base_url, tmp_path = fixture_site
    config_path = tmp_path / "crawl_targets.yaml"
    config_path.write_text(
        yaml.safe_dump(
            [
                {
                    "seed_url": f"{base_url}/index.html",
                    "allowed_groups": ["engineering"],
                    "mode": "same_origin",
                    "max_depth": 2,
                    "max_pages": 10,
                }
            ]
        )
    )
    connector = WebCrawlerConnector(config_path=config_path)  # real httpx.Client, no fake

    documents = connector.load_documents()

    sources = {doc.source for doc in documents}
    assert sources == {f"{base_url}/index.html", f"{base_url}/page2.html"}
    assert connector.skipped == [(f"{base_url}/private.html", "disallowed by robots.txt")]

    index_doc = next(d for d in documents if d.source == f"{base_url}/index.html")
    assert "# Index Page" in index_doc.text
    assert "Welcome to the local docs site." in index_doc.text
    page2_doc = next(d for d in documents if d.source == f"{base_url}/page2.html")
    assert "# Page Two" in page2_doc.text


def test_single_page_mode_against_a_real_server_ignores_the_pages_own_links(fixture_site):
    base_url, tmp_path = fixture_site
    config_path = tmp_path / "crawl_targets.yaml"
    config_path.write_text(
        yaml.safe_dump(
            [{"seed_url": f"{base_url}/index.html", "allowed_groups": ["engineering"]}]
        )
    )
    connector = WebCrawlerConnector(config_path=config_path)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {f"{base_url}/index.html"}


def test_crawled_content_is_ingested_and_acl_correctly_searchable(fixture_site):
    """The full path this connector exists for: crawl a real site ->
    chunk -> embed -> write to real Qdrant + SQLite -> only the configured
    group can find it. Mirrors tests/test_governance.py's live pattern.
    """
    from ekassistant.config.settings import Settings

    base_url, tmp_path = fixture_site
    settings = Settings(
        qdrant_collection=f"test_crawler_{uuid.uuid4().hex}",
        embedding_dimensions=len(EMBEDDING),
        keyword_index_path=tmp_path / "keyword_index.sqlite3",
    )
    if not _qdrant_is_reachable(settings.qdrant_url):
        pytest.skip("Qdrant is not reachable at " + settings.qdrant_url + " (run `make up`)")

    from ekassistant.index.keyword_index import SqliteKeywordIndex
    from ekassistant.index.vector_index import QdrantVectorIndex

    vector_index = QdrantVectorIndex(settings)
    vector_index.ensure_collection()
    keyword_index = SqliteKeywordIndex(settings)

    config_path = tmp_path / "crawl_targets.yaml"
    config_path.write_text(
        yaml.safe_dump(
            [{"seed_url": f"{base_url}/index.html", "allowed_groups": ["engineering"]}]
        )
    )
    connector = WebCrawlerConnector(config_path=config_path)

    try:
        chunk_count = run_ingest(connector, ConstantEmbedder(), vector_index, keyword_index)
        assert chunk_count > 0

        engineering_hits = keyword_index.search(
            "local docs site", allowed_groups=["engineering"], top_n=10
        )
        assert any(hit.source == f"{base_url}/index.html" for hit in engineering_hits)

        finance_hits = keyword_index.search(
            "local docs site", allowed_groups=["finance"], top_n=10
        )
        assert finance_hits == []
    finally:
        keyword_index.close()
        cleanup_client = qdrant_client_module.QdrantClient(url=settings.qdrant_url)
        cleanup_client.delete_collection(settings.qdrant_collection)
