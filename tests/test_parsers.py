import pathlib

import docx
import pytest

from ekassistant.ingest.parsers import parse_document
from ekassistant.ingest.parsers.docx_parser import parse as parse_docx
from ekassistant.ingest.parsers.html_parser import parse as parse_html
from ekassistant.ingest.parsers.pdf_parser import parse as parse_pdf
from ekassistant.ingest.parsers.text_parser import parse as parse_text

_SEED_CORPUS = pathlib.Path(__file__).parent.parent / "seed_corpus"


def test_text_parser_returns_file_contents_unchanged(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# Heading\n\nBody text.")

    assert parse_text(path) == "# Heading\n\nBody text."


def test_html_parser_converts_headings_and_paragraphs(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text(
        "<html><body>"
        "<h1>Title</h1>"
        "<h2>Section One</h2>"
        "<p>First paragraph.</p>"
        "<h2>Section Two</h2>"
        "<p>Second paragraph.</p>"
        "</body></html>"
    )

    text = parse_html(path)

    assert "# Title" in text
    assert "## Section One" in text
    assert "First paragraph." in text
    assert "## Section Two" in text
    assert "Second paragraph." in text
    # Document order is preserved, not grouped by tag type.
    assert text.index("Section One") < text.index("Section Two")


def test_html_parser_ignores_non_heading_non_paragraph_tags(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text(
        "<html><body><nav>Skip this nav</nav><h1>Title</h1>"
        "<p>Keep this.</p><script>console.log('skip')</script></body></html>"
    )

    text = parse_html(path)

    assert "Skip this nav" not in text
    assert "console.log" not in text
    assert "Keep this." in text


def test_html_parser_skips_empty_tags(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text("<html><body><h2>   </h2><p>Real content.</p></body></html>")

    text = parse_html(path)

    assert text.strip() == "Real content."


def test_docx_parser_maps_heading_styles_to_hash_prefixes(tmp_path):
    path = tmp_path / "doc.docx"
    document = docx.Document()
    document.add_heading("Title", level=1)
    document.add_heading("Subsection", level=2)
    document.add_paragraph("Body text under the subsection.")
    document.save(path)

    text = parse_docx(path)

    assert "# Title" in text
    assert "## Subsection" in text
    assert "Body text under the subsection." in text


def test_docx_parser_skips_empty_paragraphs(tmp_path):
    path = tmp_path / "doc.docx"
    document = docx.Document()
    document.add_heading("Title", level=1)
    document.add_paragraph("")
    document.add_paragraph("Real content.")
    document.save(path)

    text = parse_docx(path)

    assert text == "# Title\n\nReal content."


def test_pdf_parser_extracts_text_from_the_real_seed_corpus_fixture():
    # No lightweight, dependency-free way to author a synthetic PDF fixture
    # in a unit test (unlike .md/.html/.docx, which are just text/XML this
    # test can write directly) - see docs/decisions/0009 for how the
    # committed seed_corpus/vendor-contract-terms.pdf was produced.
    # Testing against the real committed fixture keeps this test honest
    # about what pypdf actually extracts from a real PDF.
    text = parse_pdf(_SEED_CORPUS / "vendor-contract-terms.pdf")

    assert "Vendor Contract Terms" in text
    assert "Payment terms" in text
    assert "net-45" in text


def test_parse_document_dispatches_by_extension(tmp_path):
    md_path = tmp_path / "doc.md"
    md_path.write_text("plain markdown")

    assert parse_document(md_path) == "plain markdown"


def test_parse_document_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "doc.exe"
    path.write_text("irrelevant")

    with pytest.raises(ValueError, match="unsupported file extension"):
        parse_document(path)


def test_parse_document_extension_matching_is_case_insensitive(tmp_path):
    path = tmp_path / "doc.MD"
    path.write_text("plain markdown")

    assert parse_document(path) == "plain markdown"
