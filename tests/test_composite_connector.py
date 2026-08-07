from ekassistant.ingest.connectors.composite import CompositeConnector
from ekassistant.ingest.connectors.document import Document


class FakeConnector:
    def __init__(self, documents: list[Document]):
        self._documents = documents

    def load_documents(self) -> list[Document]:
        return self._documents


def test_composite_connector_concatenates_every_sub_connectors_documents():
    a = FakeConnector([Document("a.md", "a.md", "text a", ["eng"])])
    b = FakeConnector([Document("b.md", "b.md", "text b", ["finance"])])

    documents = CompositeConnector([a, b]).load_documents()

    assert {doc.doc_id for doc in documents} == {"a.md", "b.md"}


def test_composite_connector_with_no_sub_connectors_returns_nothing():
    assert CompositeConnector([]).load_documents() == []


def test_composite_connector_with_all_empty_sub_connectors_returns_nothing():
    assert CompositeConnector([FakeConnector([]), FakeConnector([])]).load_documents() == []
