import pytest
import yaml

from ekassistant.ingest.connectors.crawler import WebCrawlerConnector


class FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class FakeHttpClient:
    """In-memory URL -> response map, standing in for a real HTTP client -
    lets every test here be fast and deterministic without touching the
    network. tests/test_crawler_live.py covers the real-HTTP-stack case
    separately, against a local fixture server.
    """

    def __init__(self, pages: dict[str, FakeResponse]):
        self._pages = pages
        self.requested_urls: list[str] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> FakeResponse:
        self.requested_urls.append(url)
        return self._pages.get(url, FakeResponse(404, ""))


def _page(title: str, *links: str) -> str:
    link_tags = "".join(f'<a href="{href}">link</a>' for href in links)
    return f"<html><body><h1>{title}</h1><p>Body of {title}.</p>{link_tags}</body></html>"


def _write_config(tmp_path, targets: list[dict]):
    path = tmp_path / "crawl_targets.yaml"
    path.write_text(yaml.safe_dump(targets))
    return path


def test_missing_config_file_returns_no_documents(tmp_path):
    connector = WebCrawlerConnector(
        config_path=tmp_path / "does-not-exist.yaml", http_client=FakeHttpClient({})
    )

    assert connector.load_documents() == []


def test_empty_config_file_returns_no_documents(tmp_path):
    config_path = tmp_path / "crawl_targets.yaml"
    config_path.write_text("")

    connector = WebCrawlerConnector(config_path=config_path, http_client=FakeHttpClient({}))

    assert connector.load_documents() == []


def test_single_page_mode_fetches_only_the_seed_url_and_ignores_its_links(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/start": FakeResponse(
                200, _page("Start", "https://example.com/other")
            ),
        }
    )
    config_path = _write_config(
        tmp_path,
        [{"seed_url": "https://example.com/start", "allowed_groups": ["engineering"]}],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert len(documents) == 1
    assert documents[0].doc_id == "https://example.com/start"
    assert documents[0].source == "https://example.com/start"
    assert documents[0].allowed_groups == ["engineering"]
    assert "# Start" in documents[0].text
    assert "https://example.com/other" not in http_client.requested_urls


def test_same_origin_mode_follows_links_within_the_same_origin(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/start": FakeResponse(
                200, _page("Start", "https://example.com/page2")
            ),
            "https://example.com/page2": FakeResponse(200, _page("Page Two")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 2,
                "max_pages": 10,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    sources = {doc.source for doc in documents}
    assert sources == {"https://example.com/start", "https://example.com/page2"}
    assert all(doc.allowed_groups == ["engineering"] for doc in documents)


def test_same_origin_mode_never_follows_a_link_to_a_different_origin(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/start": FakeResponse(
                200, _page("Start", "https://not-allowlisted.com/page")
            ),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 5,
                "max_pages": 10,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {"https://example.com/start"}
    assert "https://not-allowlisted.com/page" not in http_client.requested_urls


def test_same_origin_mode_respects_max_pages(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/p0": FakeResponse(200, _page("P0", "https://example.com/p1")),
            "https://example.com/p1": FakeResponse(200, _page("P1", "https://example.com/p2")),
            "https://example.com/p2": FakeResponse(200, _page("P2", "https://example.com/p3")),
            "https://example.com/p3": FakeResponse(200, _page("P3")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/p0",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 10,
                "max_pages": 2,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert len(documents) == 2


def test_same_origin_mode_respects_max_depth(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/p0": FakeResponse(200, _page("P0", "https://example.com/p1")),
            "https://example.com/p1": FakeResponse(200, _page("P1", "https://example.com/p2")),
            "https://example.com/p2": FakeResponse(200, _page("P2")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/p0",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 1,
                "max_pages": 10,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    # depth 0 (p0) and depth 1 (p1) are fetched; p2 is depth 2, past the cap.
    assert {doc.source for doc in documents} == {
        "https://example.com/p0",
        "https://example.com/p1",
    }


def test_links_differing_only_by_fragment_dedupe_to_one_document(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/start": FakeResponse(
                200,
                _page(
                    "Start",
                    "https://example.com/page2#section-a",
                    "https://example.com/page2#section-b",
                ),
            ),
            "https://example.com/page2": FakeResponse(200, _page("Page Two")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 2,
                "max_pages": 10,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {
        "https://example.com/start",
        "https://example.com/page2",
    }
    assert http_client.requested_urls.count("https://example.com/page2") == 1


def test_robots_txt_disallow_prevents_fetching_that_url(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/robots.txt": FakeResponse(
                200, "User-agent: *\nDisallow: /private\n"
            ),
            "https://example.com/private": FakeResponse(200, _page("Secret")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [{"seed_url": "https://example.com/private", "allowed_groups": ["engineering"]}],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert documents == []
    assert connector.skipped == [("https://example.com/private", "disallowed by robots.txt")]


def test_missing_robots_txt_is_treated_as_allow_all(tmp_path):
    # No "https://example.com/robots.txt" entry in the fake client at all -
    # a 404, same as a real site with no robots.txt.
    http_client = FakeHttpClient(
        {"https://example.com/start": FakeResponse(200, _page("Start"))}
    )
    config_path = _write_config(
        tmp_path,
        [{"seed_url": "https://example.com/start", "allowed_groups": ["engineering"]}],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert len(documents) == 1


def test_fetch_failure_is_recorded_and_skipped_not_fatal(tmp_path):
    class RaisingHttpClient:
        def get(self, url, *, headers, timeout):
            raise ConnectionError("simulated network failure")

    config_path = _write_config(
        tmp_path,
        [{"seed_url": "https://example.com/start", "allowed_groups": ["engineering"]}],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=RaisingHttpClient())

    documents = connector.load_documents()

    assert documents == []
    assert connector.skipped[0][0] == "https://example.com/start"
    assert "simulated network failure" in connector.skipped[0][1]


def test_a_404_response_is_recorded_and_skipped_not_fatal(tmp_path):
    config_path = _write_config(
        tmp_path,
        [{"seed_url": "https://example.com/does-not-exist", "allowed_groups": ["engineering"]}],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=FakeHttpClient({}))

    documents = connector.load_documents()

    assert documents == []
    assert connector.skipped == [("https://example.com/does-not-exist", "HTTP 404")]


def test_one_targets_fetch_failure_does_not_block_a_second_target(tmp_path):
    http_client = FakeHttpClient(
        {"https://example.com/good": FakeResponse(200, _page("Good"))}
    )
    config_path = _write_config(
        tmp_path,
        [
            {"seed_url": "https://example.com/broken", "allowed_groups": ["engineering"]},
            {"seed_url": "https://example.com/good", "allowed_groups": ["finance"]},
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {"https://example.com/good"}


def test_unknown_mode_raises_a_clear_error(tmp_path):
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "spider",
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=FakeHttpClient({}))

    with pytest.raises(ValueError, match="unknown crawl mode"):
        connector.load_documents()


def test_same_origin_mode_without_max_depth_or_max_pages_raises_a_clear_error(tmp_path):
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=FakeHttpClient({}))

    with pytest.raises(ValueError, match="requires both max_depth and max_pages"):
        connector.load_documents()


def test_missing_allowed_groups_fails_closed_to_nobody(tmp_path):
    http_client = FakeHttpClient({"https://example.com/start": FakeResponse(200, _page("Start"))})
    config_path = _write_config(tmp_path, [{"seed_url": "https://example.com/start"}])
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert documents[0].allowed_groups == []


def test_close_closes_an_owned_default_client_but_not_an_injected_one(tmp_path):
    config_path = _write_config(tmp_path, [])

    class TrackedFakeClient(FakeHttpClient):
        def __init__(self):
            super().__init__({})
            self.closed = False

        def close(self):
            self.closed = True

    injected_client = TrackedFakeClient()
    connector = WebCrawlerConnector(config_path=config_path, http_client=injected_client)
    connector.close()
    assert injected_client.closed is False  # caller-provided, not ours to close

    owned_connector = WebCrawlerConnector(config_path=config_path)
    owned_connector.close()  # must not raise - closes its own default httpx.Client


def test_malformed_allowed_groups_raises_a_clear_error(tmp_path):
    # A bare string instead of a list - the same manifest typo
    # FilesystemConnector's config guards against, shared via
    # validate_allowed_groups().
    config_path = _write_config(
        tmp_path,
        [{"seed_url": "https://example.com/start", "allowed_groups": "engineering"}],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=FakeHttpClient({}))

    with pytest.raises(ValueError, match="allowed_groups must be a list of strings"):
        connector.load_documents()


def test_links_differing_only_by_a_tracking_param_dedupe_to_one_document(tmp_path):
    # Found live: a same_origin crawl of a real blog indexed several
    # posts twice, once under a bare URL and once under a
    # utm_source-tagged variant of the same link.
    http_client = FakeHttpClient(
        {
            "https://example.com/start": FakeResponse(
                200,
                _page(
                    "Start",
                    "https://example.com/post",
                    "https://example.com/post?utm_source=newsletter&utm_medium=email",
                ),
            ),
            "https://example.com/post": FakeResponse(200, _page("Post")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 2,
                "max_pages": 10,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {
        "https://example.com/start",
        "https://example.com/post",
    }
    assert http_client.requested_urls.count("https://example.com/post") == 1


def test_a_tracking_param_seed_url_is_normalized_before_fetching(tmp_path):
    # The stripping applies to the configured seed_url itself, not just
    # to links discovered while crawling.
    http_client = FakeHttpClient({"https://example.com/start": FakeResponse(200, _page("Start"))})
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start?utm_campaign=launch",
                "allowed_groups": ["engineering"],
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert documents[0].source == "https://example.com/start"
    assert http_client.requested_urls == [
        "https://example.com/robots.txt",
        "https://example.com/start",
    ]


def test_a_non_tracking_query_param_is_preserved_not_stripped(tmp_path):
    # Only the known tracking-param denylist is stripped - an
    # unrecognized param (e.g. real pagination) must survive, since
    # dropping it could wrongly merge two actually-different pages.
    http_client = FakeHttpClient(
        {
            "https://example.com/start": FakeResponse(
                200, _page("Start", "https://example.com/list?page=2")
            ),
            "https://example.com/list?page=2": FakeResponse(200, _page("Page Two")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 2,
                "max_pages": 10,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {
        "https://example.com/start",
        "https://example.com/list?page=2",
    }


def test_two_different_targets_converging_on_identical_content_dedupe(tmp_path):
    # The URL-normalization fix above only helps when the duplicate is
    # reachable through a tracking-param variant of the SAME URL. This is
    # the safety net for the other case: two independently configured
    # targets whose fetched, extracted text is identical even though the
    # URLs share nothing in common (e.g. a genuine alternate/canonical
    # URL for the same article).
    http_client = FakeHttpClient(
        {
            "https://example.com/original": FakeResponse(200, _page("Same Content")),
            "https://mirror.example.org/copy": FakeResponse(200, _page("Same Content")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {"seed_url": "https://example.com/original", "allowed_groups": ["engineering"]},
            {"seed_url": "https://mirror.example.org/copy", "allowed_groups": ["engineering"]},
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert len(documents) == 1
    assert documents[0].source == "https://example.com/original"
    assert connector.skipped == [
        ("https://mirror.example.org/copy", "duplicate content of https://example.com/original")
    ]


def test_content_dedup_does_not_drop_two_genuinely_different_pages(tmp_path):
    http_client = FakeHttpClient(
        {
            "https://example.com/a": FakeResponse(200, _page("Page A")),
            "https://example.com/b": FakeResponse(200, _page("Page B")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {"seed_url": "https://example.com/a", "allowed_groups": ["engineering"]},
            {"seed_url": "https://example.com/b", "allowed_groups": ["engineering"]},
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {"https://example.com/a", "https://example.com/b"}
    assert connector.skipped == []


def test_content_dedup_does_not_merge_two_pages_that_both_extract_to_empty_text(tmp_path):
    # Two pages that both fail structural extraction (e.g. JS-rendered
    # pages with no <h1>-<h6>/<p> content - see ADR-0010's no-JS-rendering
    # tradeoff) both produce Document.text == "". They are NOT duplicates
    # of each other, just two separate failed extractions - merging them
    # would produce a misleading "duplicate content" skip reason.
    js_shell = "<html><body><div id='app'></div></body></html>"
    http_client = FakeHttpClient(
        {
            "https://example.com/a": FakeResponse(200, js_shell),
            "https://example.com/b": FakeResponse(200, js_shell),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {"seed_url": "https://example.com/a", "allowed_groups": ["engineering"]},
            {"seed_url": "https://example.com/b", "allowed_groups": ["engineering"]},
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {"https://example.com/a", "https://example.com/b"}
    assert all(doc.text == "" for doc in documents)
    assert connector.skipped == []


def test_content_dedup_applies_within_a_single_same_origin_crawl(tmp_path):
    # The cross-target case is covered above; this covers the more
    # common real-world shape - two distinct URLs on the SAME crawled
    # origin (e.g. a canonical page and a print-view mirror of it) that
    # happen to extract to identical text.
    http_client = FakeHttpClient(
        {
            "https://example.com/start": FakeResponse(
                200,
                _page(
                    "Start",
                    "https://example.com/post",
                    "https://example.com/post/print",
                ),
            ),
            "https://example.com/post": FakeResponse(200, _page("Post")),
            "https://example.com/post/print": FakeResponse(200, _page("Post")),
        }
    )
    config_path = _write_config(
        tmp_path,
        [
            {
                "seed_url": "https://example.com/start",
                "allowed_groups": ["engineering"],
                "mode": "same_origin",
                "max_depth": 2,
                "max_pages": 10,
            }
        ],
    )
    connector = WebCrawlerConnector(config_path=config_path, http_client=http_client)

    documents = connector.load_documents()

    assert {doc.source for doc in documents} == {
        "https://example.com/start",
        "https://example.com/post",
    }
    assert connector.skipped == [
        ("https://example.com/post/print", "duplicate content of https://example.com/post")
    ]
