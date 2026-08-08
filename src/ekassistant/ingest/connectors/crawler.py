"""Web crawler connector. See docs/decisions/0010-web-crawler-connector.md.

Domain-agnostic by design: nothing here names a specific site. Every
target this connector ever fetches comes from `config/crawl_targets.yaml`
(empty by default), which pairs a seed URL with its `allowed_groups` and a
crawl mode - `single_page` (fetch exactly that URL, follow nothing) or
`same_origin` (follow same-origin links, bounded by max_depth/max_pages).
An unmapped domain is never fetched at all (ADR-0002's fail-closed rule
applied to a source that, unlike every other connector, has no ACL
metadata of its own to read).

`http_client` is injected (any object with a `.get(url, *, headers,
timeout) -> Response` method - httpx.Client satisfies this structurally)
so tests can run against a local fixture server instead of the real
internet.
"""

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import yaml
from bs4 import BeautifulSoup

from ekassistant.ingest.connectors.document import Document, validate_allowed_groups
from ekassistant.ingest.parsers.html_parser import parse_html

_USER_AGENT = "ekassistant-crawler/1.0 (+see docs/decisions/0010-web-crawler-connector.md)"
_REQUEST_TIMEOUT_SECONDS = 10.0
_VALID_MODES = {"single_page", "same_origin"}

# Query params known to carry tracking/attribution metadata rather than
# identify distinct content - stripped during normalization so e.g.
# "/post" and "/post?utm_source=newsletter" dedupe to one document
# instead of being crawled and indexed as two copies of the same page
# (found live: a same_origin crawl of a real blog indexed several posts
# twice, once under a bare URL and once under a utm_source-tagged variant
# of the same link, wasting retrieval's fixed top-k slots on duplicate
# content). Deliberately a narrow, well-known denylist, not "strip every
# query param" - an unrecognized param might be load-bearing (e.g. a
# genuine pagination or article-id parameter), and dropping it could
# silently merge two actually-different pages into one.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref_src",
    }
)


class Response(Protocol):
    status_code: int
    text: str


class HttpFetcher(Protocol):
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> Response: ...


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _normalize_url(url: str) -> str:
    # Strips the fragment (an in-page anchor, not a distinct resource) so
    # "page#section-a" and "page#section-b" dedupe to one document, and
    # strips known tracking query params (see _TRACKING_PARAMS) so a
    # tagged link variant dedupes with the same page's bare URL. Query
    # param order among the params that remain is preserved, since some
    # servers (rarely, but not never) are order-sensitive.
    parts = urlsplit(url)
    kept_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in _TRACKING_PARAMS
    ]
    query = urlencode(kept_params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _extract_same_origin_links(html: str, base_url: str, origin: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        resolved = urljoin(base_url, tag["href"])
        if not resolved.startswith(("http://", "https://")):
            continue  # skip mailto:, javascript:, tel:, ...
        normalized = _normalize_url(resolved)
        if _origin(normalized) != origin or normalized in seen:
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


class _RobotsCache:
    """Fetches and caches robots.txt per origin via the injected HTTP
    client (not RobotFileParser's own urllib-based .read(), which would
    bypass the injected fetcher and hit the real network in tests). A
    missing or unfetchable robots.txt is treated as "allow all", matching
    the common convention for a 404 - not distinguishing that from a
    server error is a deliberate simplification for this POC's scope.
    """

    def __init__(self, http_client: HttpFetcher, user_agent: str):
        self._http_client = http_client
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser] = {}

    def can_fetch(self, url: str) -> bool:
        origin = _origin(url)
        parser = self._parsers.get(origin)
        if parser is None:
            parser = RobotFileParser()
            try:
                response = self._http_client.get(
                    origin + "/robots.txt",
                    headers={"User-Agent": self._user_agent},
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            except Exception:
                parser.parse([])
            self._parsers[origin] = parser
        return parser.can_fetch(self._user_agent, url)


@dataclass(frozen=True)
class _Target:
    seed_url: str
    allowed_groups: list[str]
    mode: str
    max_depth: int | None
    max_pages: int | None


def _load_targets(config_path: Path) -> list[_Target]:
    if not config_path.exists():
        return []
    raw_targets = yaml.safe_load(config_path.read_text()) or []
    targets = []
    for raw in raw_targets:
        seed_url = raw["seed_url"]
        mode = raw.get("mode", "single_page")
        if mode not in _VALID_MODES:
            raise ValueError(f"{seed_url}: unknown crawl mode {mode!r} (expected {_VALID_MODES})")
        max_depth = raw.get("max_depth")
        max_pages = raw.get("max_pages")
        if mode == "same_origin" and (max_depth is None or max_pages is None):
            raise ValueError(
                f"{seed_url}: mode 'same_origin' requires both max_depth and max_pages"
            )
        targets.append(
            _Target(
                seed_url=seed_url,
                allowed_groups=validate_allowed_groups(seed_url, raw.get("allowed_groups")),
                mode=mode,
                max_depth=max_depth,
                max_pages=max_pages,
            )
        )
    return targets


class WebCrawlerConnector:
    def __init__(self, config_path: Path, http_client: HttpFetcher | None = None):
        self._config_path = config_path
        # Only close the client in close() below if we constructed it
        # ourselves - an injected client (a test fake, or a caller-managed
        # httpx.Client) is the caller's to close, not ours.
        self._owned_client: httpx.Client | None = None
        if http_client is None:
            self._owned_client = httpx.Client()
            http_client = self._owned_client
        self._http_client: HttpFetcher = http_client
        self._robots = _RobotsCache(self._http_client, _USER_AGENT)
        # Every skipped URL and why - not silently swallowed, so a caller
        # (the CLI, a test) can see what didn't make it in and why. See
        # docs/decisions/0010: fetch failures are non-fatal to the run.
        self.skipped: list[tuple[str, str]] = []

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()

    def load_documents(self) -> list[Document]:
        documents: list[Document] = []
        for target in _load_targets(self._config_path):
            if target.mode == "single_page":
                documents.extend(self._fetch_single_page(target))
            else:
                documents.extend(self._crawl_same_origin(target))
        return self._dedup_by_content(documents)

    def _dedup_by_content(self, documents: list[Document]) -> list[Document]:
        # A safety net beyond _normalize_url's tracking-param stripping:
        # catches duplicate content reached via genuinely different URLs
        # that URL normalization can't detect since it only ever looks at
        # the URL, never the fetched content - e.g. two separately
        # configured targets (different seed_url entries in
        # config/crawl_targets.yaml) that happen to converge on the same
        # canonical page. Whichever copy is encountered first (crawl
        # order) wins; later duplicates are dropped and recorded in
        # `skipped`, not silently discarded.
        #
        # Empty extracted text is deliberately exempt: two pages that both
        # extract to "" (e.g. JS-rendered pages html_parser.parse_html
        # can't read structure from - see ADR-0010's no-JS-rendering
        # tradeoff) aren't actually duplicates of each other, just two
        # separate failed extractions. Merging them would produce a
        # `skipped` entry claiming "duplicate content", which is
        # misleading when debugging why a page didn't get indexed.
        first_seen_at_by_text: dict[str, str] = {}
        deduped: list[Document] = []
        for document in documents:
            if not document.text:
                deduped.append(document)
                continue
            first_seen_at = first_seen_at_by_text.get(document.text)
            if first_seen_at is not None:
                self.skipped.append((document.source, f"duplicate content of {first_seen_at}"))
                continue
            first_seen_at_by_text[document.text] = document.source
            deduped.append(document)
        return deduped

    def _fetch_single_page(self, target: _Target) -> list[Document]:
        url = _normalize_url(target.seed_url)
        html = self._fetch_html(url)
        if html is None:
            return []
        return [self._to_document(url, html, target.allowed_groups)]

    def _crawl_same_origin(self, target: _Target) -> list[Document]:
        seed_url = _normalize_url(target.seed_url)
        origin = _origin(seed_url)
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        visited: set[str] = set()
        documents: list[Document] = []

        while queue and len(documents) < target.max_pages:
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            html = self._fetch_html(url)
            if html is None:
                continue
            documents.append(self._to_document(url, html, target.allowed_groups))

            if depth < target.max_depth:
                for link in _extract_same_origin_links(html, url, origin):
                    if link not in visited:
                        queue.append((link, depth + 1))

        return documents

    def _to_document(self, url: str, html: str, allowed_groups: list[str]) -> Document:
        return Document(
            doc_id=url, source=url, text=parse_html(html), allowed_groups=allowed_groups
        )

    def _fetch_html(self, url: str) -> str | None:
        if not self._robots.can_fetch(url):
            self.skipped.append((url, "disallowed by robots.txt"))
            return None
        try:
            response = self._http_client.get(
                url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT_SECONDS
            )
        except Exception as exc:
            self.skipped.append((url, f"fetch failed: {exc}"))
            return None
        if response.status_code != 200:
            self.skipped.append((url, f"HTTP {response.status_code}"))
            return None
        return response.text
