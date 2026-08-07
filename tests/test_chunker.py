from ekassistant.ingest.chunker import chunk_document

HEADING_DOC = """# Title

# Overview
Some overview text.

# Requirements
Some requirement text.
"""


def test_splits_on_heading_boundaries():
    chunks = chunk_document("doc.md", "doc.md", HEADING_DOC, ["engineering"])

    texts = [c.text for c in chunks]
    assert any("Overview" in t for t in texts)
    assert any("Requirements" in t for t in texts)
    # Each heading's content stays together in one chunk, not split apart.
    overview_chunk = next(t for t in texts if "Overview" in t)
    assert "Some overview text." in overview_chunk


def test_chunk_ids_are_stable_and_ordered():
    chunks = chunk_document("doc.md", "doc.md", HEADING_DOC, ["engineering"])

    assert [c.chunk_id for c in chunks] == [f"doc.md#{i}" for i in range(len(chunks))]


def test_every_chunk_carries_the_document_source_and_acl():
    chunks = chunk_document("doc.md", "doc.md", HEADING_DOC, ["engineering", "all-staff"])

    assert all(c.source == "doc.md" for c in chunks)
    assert all(c.allowed_groups == ["engineering", "all-staff"] for c in chunks)


def test_long_section_is_split_on_paragraph_boundaries():
    long_section = "# Long\n\n" + "\n\n".join(f"Paragraph {i} " + "x" * 100 for i in range(20))

    chunks = chunk_document("doc.md", "doc.md", long_section, [])

    assert len(chunks) > 1
    assert all(len(c.text) <= 900 for c in chunks)  # _MAX_CHARS=800 plus small slack


def test_empty_document_produces_no_chunks():
    chunks = chunk_document("doc.md", "doc.md", "", ["engineering"])

    assert chunks == []


def test_document_with_no_headings_is_still_chunked():
    chunks = chunk_document("doc.md", "doc.md", "Just plain text, no heading at all.", [])

    assert len(chunks) == 1
    assert chunks[0].text == "Just plain text, no heading at all."
