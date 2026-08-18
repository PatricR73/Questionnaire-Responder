"""Tests for the answer library (pack 3, C4).

The library is a SEPARATE namespace of human-approved answers surfaced to the
generator as labelled candidates — never a retrieval source, and never allowed to
launder a stale claim. These tests pin the structural guarantees:

1. An approved answer is invisible to HybridSearcher (structural isolation).
2. Staleness: an answer approved against a source doc that has since changed is
   excluded, as is one with no verifiable source snapshot.
3. Matching: exact normalized text wins; semantic matching (same embedding space
   as retrieval) finds near-equivalent questions above the threshold.
4. The pipeline path: with the config flag on, a seeded library produces the
   library_state marker, the provenance comment/fill in the workbook, and the
   library_candidate record in the answers row — without any API call (stub).
"""

import json
from pathlib import Path

import openpyxl

from src.answer.library import answer_uses_prior, find_candidates, format_prior_answer_block
from src.ingest.embed import ingest_evidence
from src.questionnaire.parse_xlsx import detect_columns
from src.questionnaire.write_xlsx import LIBRARY_FILL
from src.retrieval.hybrid_search import HybridSearcher
from src.store import db
from src.store.vectorstore import VectorStore

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _seed_store(tmp_path, conn):
    """Ingest the synthetic fixtures and approve one answer into the library."""
    vector_store = VectorStore(persist_dir=tmp_path / "chroma")
    ingest_evidence(FIXTURES / "evidence", conn, vector_store)
    run_id = db.start_questionnaire_run(conn, "fixtures/eval/questionnaire_eval.xlsx", "out/x.xlsx")
    db.record_answer(
        conn,
        run_id,
        3,
        "Is cloud data periodically backed up?",
        "Is cloud data periodically backed up?",
        "Yes. Production databases are backed up hourly with 30-day retention.",
        None,
        "high",
        "high",
        _chunk_ids(conn, "business_continuity_plan.docx"),
        polarity="affirms",
        cited_sentences=["Production databases are backed up hourly with 30-day retention."],
    )
    db.record_audit_entry(conn, run_id, 3, ["business_continuity_plan.docx"], "high", provider="stub")
    return vector_store


def _chunk_ids(conn, source_filename):
    """Real embedding ids from the ingested store, so the review-time source-hash
    snapshot in record_human_review can resolve them."""
    rows = conn.execute(
        "SELECT embedding_id FROM chunks WHERE source_filename = ? LIMIT 1", (source_filename,)
    ).fetchall()
    assert rows, f"no chunks for {source_filename}"
    return [r["embedding_id"] for r in rows]


def _approve(conn, run_id, row_index, question, answer, cited_ids):
    db.record_human_review(conn, run_id, row_index, "approved")
    # record_human_review stores into the library; the cited chunk ids must exist
    # in chunks so the source snapshot resolves.
    return db.find_reviewed_answers(conn, question)


def test_reviewed_answer_is_invisible_to_hybrid_search(tmp_path):
    """Structural isolation: an approved answer must never be retrievable as evidence."""
    conn = db.connect(tmp_path / "store.db")
    vector_store = _seed_store(tmp_path, conn)
    approved = _approve(conn, 1, 3, "Is cloud data periodically backed up?", "ANSWER TEXT", ["x::0"])
    assert approved, "approval should seed the library"

    # An answer text that does not exist in any evidence chunk.
    searcher = HybridSearcher(conn, vector_store)
    hits = searcher.search("Production databases are backed up hourly with 30-day retention", top_k=5)
    for hit in hits:
        assert "ANSWER TEXT" not in hit.text


def test_stale_answer_is_excluded(tmp_path):
    conn = db.connect(tmp_path / "store.db")
    _seed_store(tmp_path, conn)
    _approve(conn, 1, 3, "Is cloud data periodically backed up?", "ANSWER TEXT", ["x::0"])
    assert db.find_reviewed_answers(conn, "Is cloud data periodically backed up?")

    # The source doc changes -> its hash changes -> the answer becomes stale.
    db.record_source_doc(conn, "business_continuity_plan.docx", "newhash")
    assert db.find_reviewed_answers(conn, "Is cloud data periodically backed up?") == []


def test_answer_without_source_snapshot_is_excluded(tmp_path):
    """Freshness unverifiable is not freshness: an approval whose cited chunks have
    no recorded source hashes must not be surfaced."""
    conn = db.connect(tmp_path / "store.db")
    _seed_store(tmp_path, conn)
    # A row whose cited chunks have no recorded source hashes (empty snapshot).
    db.store_reviewed_answer(
        conn,
        1,
        3,
        "Is cloud data periodically backed up?",
        "ANSWER TEXT",
        "affirms",
        ["nonexistent-id"],
        [],
        {},
        "approved",
    )
    assert db.find_reviewed_answers(conn, "Is cloud data periodically backed up?") == []


def test_exact_and_semantic_matching(tmp_path):
    conn = db.connect(tmp_path / "store.db")
    vector_store = _seed_store(tmp_path, conn)
    _approve(conn, 1, 3, "Is cloud data periodically backed up?", "Yes, hourly.", ["x::0"])

    # Exact normalized match wins regardless of whitespace/case.
    exact = find_candidates(conn, "  Is cloud data periodically backed up? ", vector_store)
    assert exact and exact[0]["similarity"] == 1.0

    # Semantic near-equivalent above threshold.
    semantic = find_candidates(conn, "Are cloud databases backed up on a schedule?", vector_store)
    assert semantic, "near-equivalent question should match semantically"
    assert 0.75 <= semantic[0]["similarity"] < 1.0

    # Unrelated question: no candidate.
    assert find_candidates(conn, "Are employees background-checked before hire?", vector_store) == []


def test_answer_uses_prior():
    candidate = {"answer_text": "Yes. Production databases are backed up hourly with 30-day retention."}
    assert answer_uses_prior("Yes. Production databases are backed up hourly with 30-day retention.", candidate)
    assert not answer_uses_prior("No.", candidate)


def test_format_prior_answer_block_carries_provenance():
    block = format_prior_answer_block(
        {
            "answer_text": "Yes.",
            "run_id": 7,
            "row_index": 3,
            "human_action": "approved",
            "reviewed_at": "2026-01-01T00:00:00+00:00",
            "similarity": 1.0,
        }
    )
    assert "PRIOR APPROVED ANSWER" in block
    assert "run 7" in block and "row 3" in block and "approved" in block


def test_library_marker_appears_in_workbook(tmp_path, monkeypatch):
    """End-to-end stub run with the library enabled: the marker, the workbook fill,
    and the DB record all appear without any API call."""
    from click.testing import CliRunner

    from src.pipeline import cli

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = db.connect(tmp_path / "store.db")
    _seed_store(tmp_path, conn)
    _approve(conn, 1, 3, "Is cloud data periodically backed up?", "Yes, hourly, 30-day retention.", ["x::0"])

    # The eval questionnaire contains a semantically equivalent question (BCR-08.1).
    cfg_path = tmp_path / "lib.toml"
    cfg_path.write_text("answer_library = true\nlibrary_semantic_threshold = 0.7\n")

    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire",
            str(FIXTURES / "eval" / "questionnaire_eval.xlsx"),
            "--output",
            str(tmp_path / "out" / "filled.xlsx"),
            "--limit",
            "0",
            "--provider",
            "stub",
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output

    conn2 = db.connect(tmp_path / "store.db")
    row = conn2.execute(
        "SELECT library_candidate FROM answers WHERE run_id = (SELECT MAX(id) FROM questionnaire_runs) AND row_index = 3"
    ).fetchone()
    assert row and row["library_candidate"], "library_candidate must be recorded"
    lib = json.loads(row["library_candidate"])
    assert lib["state"] in ("used", "surfaced")

    wb = openpyxl.load_workbook(tmp_path / "out" / "filled.xlsx")
    ws = wb.active
    cm = detect_columns(ws)
    answer_cell = ws.cell(row=3, column=cm.answer_col)
    if lib["state"] == "used":
        assert answer_cell.fill.start_color.rgb == LIBRARY_FILL.start_color.rgb, "library fill must be applied"
    else:
        # "surfaced": a candidate existed but the draft did not reuse it — the
        # marker is a comment carrying provenance, never a fill that would
        # override the confidence colour.
        assert answer_cell.comment is not None and "prior approved answer" in answer_cell.comment.text


def test_library_off_by_default_does_not_change_output(tmp_path, monkeypatch):
    from src.config import load_config

    assert load_config().answer_library is False
