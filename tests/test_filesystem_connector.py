from ekassistant.ingest.connectors.filesystem import FilesystemConnector


def test_loads_documents_per_manifest_with_their_acl(tmp_path):
    (tmp_path / "a.md").write_text("Document A body.")
    (tmp_path / "b.md").write_text("Document B body.")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "a.md:\n  allowed_groups: [engineering]\nb.md:\n  allowed_groups: [finance, all-staff]\n"
    )

    docs = FilesystemConnector(tmp_path, manifest).load_documents()

    by_id = {d.doc_id: d for d in docs}
    assert by_id["a.md"].text == "Document A body."
    assert by_id["a.md"].allowed_groups == ["engineering"]
    assert by_id["b.md"].allowed_groups == ["finance", "all-staff"]


def test_manifest_entry_missing_allowed_groups_fails_closed(tmp_path):
    (tmp_path / "a.md").write_text("Document A body.")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("a.md: {}\n")

    docs = FilesystemConnector(tmp_path, manifest).load_documents()

    assert docs[0].allowed_groups == []


def test_empty_manifest_loads_no_documents(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("")

    docs = FilesystemConnector(tmp_path, manifest).load_documents()

    assert docs == []


def test_seed_corpus_manifest_matches_files_on_disk_and_known_groups():
    """The real seed_corpus/ shipped in this repo, not a fixture - catches
    a manifest/filesystem drift (typo'd filename, orphaned file) or a
    group ID that doesn't exist in config/identities.yaml.
    """
    import pathlib

    import yaml

    repo_root = pathlib.Path(__file__).parent.parent
    corpus_dir = repo_root / "seed_corpus"
    manifest_path = corpus_dir / "manifest.yaml"
    identities = yaml.safe_load((repo_root / "config" / "identities.yaml").read_text())
    known_groups = {g for entry in identities.values() for g in entry.get("groups", [])}

    docs = FilesystemConnector(corpus_dir, manifest_path).load_documents()

    assert len(docs) > 0
    for doc in docs:
        assert doc.text.strip() != ""
        assert doc.allowed_groups, f"{doc.doc_id} has no ACL - would be invisible to everyone"
        assert set(doc.allowed_groups) <= known_groups, (
            f"{doc.doc_id} references a group not in config/identities.yaml"
        )
