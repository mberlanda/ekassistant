"""Combines multiple connectors into one for a single run_ingest() call.

Why this matters, not just convenience: run_ingest()'s delete-propagation
(docs/decisions - see ingest/pipeline.py's module docstring, and
docs/roadmap.md item 9) treats any source it didn't see this run as
removed, and deletes it. Calling run_ingest() once per connector against
the SAME shared indexes breaks that: the second call's connector has no
visibility into the first connector's sources, so they look "removed" and
get deleted - discovered live while wiring the crawler connector in
alongside the filesystem one (docs/decisions/0010-web-crawler-connector.md).
Running every connector through exactly one run_ingest() call, via this
wrapper, keeps delete-propagation's "current_sources" the true union
across every active source, not just one connector's.
"""

from typing import Protocol

from ekassistant.ingest.connectors.document import Document


class _Connector(Protocol):
    def load_documents(self) -> list[Document]: ...


class CompositeConnector:
    def __init__(self, connectors: list[_Connector]):
        self._connectors = connectors

    def load_documents(self) -> list[Document]:
        documents = []
        for connector in self._connectors:
            documents.extend(connector.load_documents())
        return documents
