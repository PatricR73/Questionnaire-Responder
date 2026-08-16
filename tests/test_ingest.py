"""P3 regression coverage: re-ingesting must not leave stale chunks behind, and
same-filename-different-subdirectory sources must not collide.

Two bugs this locks in:

1. Chunk ids were keyed on path.name and nothing ever deleted, so a source document
   edited to produce fewer chunks left its trailing chunks in both SQLite and Chroma
   forever — a control deleted from a policy stayed retrievable and citable into a
   customer-facing questionnaire. Ingest now keys chunks by the evidence-relative
   path and deletes every existing row/entry for a source before re-inserting it.

2. hr/policy.md and eng/policy.md collided on the bare filename and silently
   overwrote each other.
"""

from src.ingest.embed import ingest_evidence
from src.store import db
from src.store.vectorstore import VectorStore


def _make_evidence_dir(tmp_path, files: dict[str, str]):
    """Write {relative_path: content} files under a fresh evidence dir."""
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        path = evidence_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return evidence_dir


def test_shortened_rewrite_removes_stale_chunks_from_both_stores(tmp_path):
    # Long version: 7 sections, each big enough to be its own chunk (chunking groups
    # by heading, so each "# Section N" becomes one chunk).
    long_policy = "\n".join(
        f"# Section {i}\n\nThis is paragraph content for section {i} of the access control policy. "
        "It describes a specific control that may later be removed from the document entirely."
        for i in range(1, 8)
    )
    evidence_dir = _make_evidence_dir(tmp_path, {"policy.md": long_policy})
    conn = db.connect(tmp_path / "store.db")
    vector_store = VectorStore(persist_dir=tmp_path / "chroma")

    n_long = ingest_evidence(evidence_dir, conn, vector_store)
    assert n_long > 1
    long_ids = {r["embedding_id"] for r in conn.execute("SELECT embedding_id FROM chunks").fetchall()}
    assert len(long_ids) == n_long

    # Rewrite the same file shorter — sections 3-7 are gone from the document.
    short_policy = "\n".join(
        f"# Section {i}\n\nThis is paragraph content for section {i} of the access control policy." for i in range(1, 3)
    )
    evidence_dir = _make_evidence_dir(tmp_path, {"policy.md": short_policy})
    n_short = ingest_evidence(evidence_dir, conn, vector_store)
    assert n_short < n_long

    short_ids = {r["embedding_id"] for r in conn.execute("SELECT embedding_id FROM chunks").fetchall()}
    removed_ids = long_ids - short_ids
    assert removed_ids, "expected the shortened rewrite to drop at least one chunk"

    # SQLite: no chunk from the removed content survives.
    assert len(short_ids) == n_short

    # Vector store: the stale ids are gone and the count equals the new total.
    assert vector_store.get(sorted(removed_ids)) == []
    assert vector_store.count() == n_short

    # And the surviving content is what the new version actually contains.
    surviving_text = conn.execute("SELECT text FROM chunks").fetchall()
    assert all("section 1" in row["text"] or "section 2" in row["text"] for row in surviving_text)


def test_same_filename_in_different_subdirectories_do_not_collide(tmp_path):
    evidence_dir = _make_evidence_dir(
        tmp_path,
        {
            "hr/policy.md": "# HR Policy\n\nBackground checks are performed for all employees before hire.",
            "eng/policy.md": "# Engineering Policy\n\nAPI secrets are rotated every 90 days.",
        },
    )
    conn = db.connect(tmp_path / "store.db")
    vector_store = VectorStore(persist_dir=tmp_path / "chroma")

    n = ingest_evidence(evidence_dir, conn, vector_store)

    rows = conn.execute("SELECT source_filename, embedding_id FROM chunks").fetchall()
    sources = {r["source_filename"] for r in rows}
    assert sources == {"hr/policy.md", "eng/policy.md"}
    ids = {r["embedding_id"] for r in rows}
    assert len(ids) == n, "same-named files must not overwrite each other's chunk ids"

    # Both documents' content is retrievable from the vector store.
    for probe in ("Background checks are performed", "API secrets are rotated"):
        hits = vector_store.query(probe, top_k=1)
        assert any(probe in h["text"] for h in hits)
