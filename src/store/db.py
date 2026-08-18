"""SQLite schema and connection helper for chunk metadata, questionnaire runs, and the audit log."""

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.data_dir import data_dir

DEFAULT_DB_PATH = data_dir() / "store.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_filename TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    loc_ref TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS questionnaire_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES questionnaire_runs(id),
    row_index INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    sub_question_text TEXT NOT NULL,
    drafted_answer TEXT,
    vocab_selection TEXT,
    self_confidence TEXT,
    final_confidence TEXT NOT NULL,
    polarity TEXT,
    cited_chunk_ids TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES questionnaire_runs(id),
    row_index INTEGER NOT NULL,
    sources_consulted TEXT,
    confidence TEXT NOT NULL,
    provider TEXT,
    human_action TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_filename TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

-- C4: the answer library — a SEPARATE namespace of human-approved answers, never a
-- retrieval source for the document evidence. HybridSearcher reads only the chunks
-- table, so an approved answer is structurally impossible to retrieve as evidence;
-- it is surfaced to the generator as a labelled CANDIDATE with provenance, and the
-- citation/entailment checks still run against the original evidence. source_doc_hashes
-- snapshots the content hashes of the source docs the answer was grounded in, so
-- find_reviewed_answers can exclude entries whose source docs have since changed —
-- a prior answer is a strong prior, not a source of truth, and must never launder a
-- stale claim into a fresh questionnaire.
CREATE TABLE IF NOT EXISTS reviewed_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    polarity TEXT,
    cited_chunk_ids TEXT NOT NULL,
    cited_sentences TEXT NOT NULL,
    source_doc_hashes TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    row_index INTEGER NOT NULL,
    human_action TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviewed_answers_question ON reviewed_answers(question_text);
"""


def connect(db_path: Path | None = None, key: str | None = None) -> sqlite3.Connection:
    """Open the store, creating schema if needed.

    Defaults to data_dir()/store.db — resolved at call time, not import time, so a
    QRESP_DATA_DIR change (or a foreign cwd) takes effect for the next connect.
    Keeping an explicit-path override is what lets tests and the eval harness point
    at an isolated database.

    Optional at-rest encryption (pack 3, C11): when key is given (or
    QRESP_STORE_KEY is set), the store is opened with SQLCipher via the
    sqlcipher3 module and a PRAGMA key, so the file on disk is unreadable without
    the key — verified: plain sqlite3 and wrong-key opens both fail. The key must
    be supplied on EVERY open of that store; losing it loses the data. The Chroma
    vector index is NOT covered (it stores the same chunk text); full at-rest
    protection for the whole data directory is disk-level encryption — see
    docs/SECURITY-POSTURE.md."""
    if db_path is None:
        db_path = data_dir() / "store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if key is None:
        key = os.environ.get("QRESP_STORE_KEY") or None
    if key:
        import sqlcipher3  # type: ignore[import-untyped]

        # PRAGMA key takes a quoted literal; keys are validated alphanumeric at
        # the call site (see pipeline.purge / SECURITY-POSTURE.md) so the single
        # quotes below are safe.
        conn = sqlcipher3.connect(str(db_path))  # type: ignore[no-untyped-call]
        conn.execute(f"PRAGMA key = '{key}'")
        # Verify the key BEFORE running the schema: with a wrong key, sqlcipher3's
        # executescript raises an unhelpful MemoryError, while this read raises a
        # clean DatabaseError — callers (and tests) get the right failure.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        # sqlcipher3 cursors require sqlcipher3.Row, not sqlite3.Row — the two
        # backends are drop-in at the API level but not at the type level.
        conn.row_factory = sqlcipher3.Row  # type: ignore[assignment]
    else:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for statement in (
        "ALTER TABLE audit_log ADD COLUMN provider TEXT",
        "ALTER TABLE answers ADD COLUMN polarity TEXT",
        # Human-edited text from the review UI. Kept separate from drafted_answer,
        # which must stay exactly what the model produced — the eval harness scores
        # against drafted_answer, and overwriting it would make past baselines
        # unreproducible. Also gives a free model-vs-human diff for a future
        # feedback-loop slice.
        "ALTER TABLE answers ADD COLUMN reviewed_answer TEXT",
        # JSON snapshot of the configuration that produced this run (model,
        # thresholds, fusion constants, chunk bounds, embedding model, git revision)
        # — see pipeline._current_run_config. Nothing ties a past run's artifacts to
        # its config otherwise, which makes old runs in out/store.db uninterpretable
        # for a project whose central claim is that every change is measured against
        # a known baseline.
        "ALTER TABLE questionnaire_runs ADD COLUMN run_config TEXT",
        # The model's verbatim cited sentences (JSON list). The review UI discards
        # the most useful evidence it has without these — the specific sentences the
        # model says support the answer — so they are persisted and highlighted in
        # the displayed chunk text.
        "ALTER TABLE answers ADD COLUMN cited_sentences TEXT",
        # C4: JSON snapshot of the answer-library state for this row (state,
        # provenance, and the candidates surfaced with their similarity scores) —
        # recorded so the review UI can show why a row was marked, and the eval can
        # measure the library's effect.
        "ALTER TABLE answers ADD COLUMN library_candidate TEXT",
        # Multi-sheet workbooks (pack 3, C6): rows from different sheets of one
        # questionnaire share a run; the sheet name keeps them distinguishable.
        "ALTER TABLE answers ADD COLUMN sheet_name TEXT",
    ):
        try:
            conn.execute(statement)
            conn.commit()
        except Exception:  # noqa: BLE001, S110 — additive ALTERs: any failure here is "column already exists" on a pre-existing database. Broad on purpose: the sqlcipher3 path raises its own OperationalError class that is NOT a sqlite3.OperationalError subclass, and this loop must be idempotent on both connection types.
            pass
    return conn


def start_questionnaire_run(
    conn: sqlite3.Connection,
    source_path: str,
    output_path: str,
    run_config: dict | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO questionnaire_runs (source_path, output_path, created_at, run_config) VALUES (?, ?, ?, ?)",
        (source_path, output_path, datetime.now(UTC).isoformat(), json.dumps(run_config or {})),
    )
    conn.commit()
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("INSERT into questionnaire_runs did not return a row id")
    return lastrowid


def record_answer(
    conn: sqlite3.Connection,
    run_id: int,
    row_index: int,
    question_text: str,
    sub_question_text: str,
    drafted_answer: str | None,
    vocab_selection: str | None,
    self_confidence: str | None,
    final_confidence: str,
    cited_chunk_ids: list[str],
    polarity: str | None = None,
    cited_sentences: list[str] | None = None,
    library_candidate: dict | None = None,
    sheet_name: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO answers (run_id, row_index, question_text, sub_question_text, drafted_answer, "
        "vocab_selection, self_confidence, final_confidence, polarity, cited_chunk_ids, cited_sentences, library_candidate, sheet_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            row_index,
            question_text,
            sub_question_text,
            drafted_answer,
            vocab_selection,
            self_confidence,
            final_confidence,
            polarity,
            json.dumps(cited_chunk_ids),
            json.dumps(cited_sentences or []),
            json.dumps(library_candidate) if library_candidate else None,
            sheet_name,
        ),
    )
    conn.commit()


def record_source_doc(conn: sqlite3.Connection, source_filename: str, content_hash: str) -> None:
    """Record (or refresh) the content hash of one ingested source document.

    The hash is what the answer library compares against when deciding whether a
    previously approved answer is still current: an answer approved against policy
    v3 must not be surfaced after policy v4 lands (C4). Hashes are recomputed from
    the file at ingest time; see ingest/embed.py."""
    conn.execute(
        "INSERT INTO source_docs (source_filename, content_hash, ingested_at) VALUES (?, ?, ?) "
        "ON CONFLICT(source_filename) DO UPDATE SET content_hash = excluded.content_hash, ingested_at = excluded.ingested_at",
        (source_filename, content_hash, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def current_source_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """filename -> current content hash, for staleness checks against the library."""
    return {
        r["source_filename"]: r["content_hash"]
        for r in conn.execute("SELECT source_filename, content_hash FROM source_docs")
    }


def store_reviewed_answer(
    conn: sqlite3.Connection,
    run_id: int,
    row_index: int,
    question_text: str,
    answer_text: str,
    polarity: str | None,
    cited_chunk_ids: list[str],
    cited_sentences: list[str],
    source_doc_hashes: dict[str, str],
    human_action: str,
) -> int:
    """Persist a human-approved/edited answer into the separate reviewed-answers namespace.

    Called from the review UI whenever a row is approved or edited, so the library
    compounds across questionnaires — the feature that makes the second questionnaire
    cheaper than the first (C4). This is explicitly NOT a retrieval source for the
    document evidence (HybridSearcher reads only chunks); it is a candidate pool for
    the generator, gated by freshness against the source-doc hashes snapshot here."""
    cursor = conn.execute(
        "INSERT INTO reviewed_answers (question_text, answer_text, polarity, cited_chunk_ids, cited_sentences, "
        "source_doc_hashes, run_id, row_index, human_action, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            question_text,
            answer_text,
            polarity,
            json.dumps(cited_chunk_ids),
            json.dumps(cited_sentences),
            json.dumps(source_doc_hashes),
            run_id,
            row_index,
            human_action,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("INSERT into reviewed_answers did not return a row id")
    return lastrowid


def find_reviewed_answers(conn: sqlite3.Connection, question_text: str, limit: int = 3) -> list[dict]:
    """Exact (normalized) prior approved/edited answers for a question, freshness-gated.

    Staleness rule (the design constraint that makes the library safe, C4): an entry
    whose source docs have since changed is EXCLUDED — a prior answer is a strong
    prior, not a source of truth, and surfacing a claim approved against old policy
    would launder a stale statement. Entries with no snapshot (reviewed before source
    hashes existed, or hashes unknowable) are also excluded: freshness unverifiable
    is not freshness. Semantic matching happens in src/answer/library.py, which
    calls this for the exact-match fast path and embeds on the fly for the rest."""
    norm = " ".join(question_text.split()).casefold()
    latest = current_source_hashes(conn)
    rows = conn.execute(
        "SELECT id, question_text, answer_text, polarity, source_doc_hashes, run_id, row_index, "
        "human_action, reviewed_at, cited_sentences FROM reviewed_answers "
        "WHERE question_text = ? ORDER BY reviewed_at DESC LIMIT ?",
        (question_text, limit),
    ).fetchall()
    out = []
    for r in rows:
        # Exact match on normalized text so phrasing/whitespace differences don't
        # hide a prior answer (exact question text comes straight from the workbook
        # most of the time).
        if " ".join(r["question_text"].split()).casefold() != norm:
            continue
        hashes = json.loads(r["source_doc_hashes"]) if r["source_doc_hashes"] else {}
        if not hashes:
            continue
        if any(latest.get(fname) != h for fname, h in hashes.items()):
            continue
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def count_reviewed_answers(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM reviewed_answers").fetchone()["n"]


def record_audit_entry(
    conn: sqlite3.Connection,
    run_id: int,
    row_index: int,
    sources_consulted: list[str],
    confidence: str,
    provider: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (run_id, row_index, sources_consulted, confidence, provider, human_action, timestamp) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?)",
        (
            run_id,
            row_index,
            json.dumps(sources_consulted),
            confidence,
            provider,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()


def record_human_review(
    conn: sqlite3.Connection,
    run_id: int,
    row_index: int,
    human_action: str | None,
    reviewed_answer: str | None = None,
) -> None:
    """Record a reviewer's decision for one row. Used only by the review UI.

    human_action is one of "approved" | "edited" | "rejected" — or None to append
    an "unreviewed" event (the UI's undo). reviewed_answer is only ever set on
    "edited" — drafted_answer (the model's original output) is never touched, so
    eval-harness scoring against it stays reproducible.

    P29: APPENDS a review event to audit_log instead of overwriting the previous
    one. Overwriting destroyed the review trail — a misclick was unrecoverable and
    the history a future feedback loop would want was gone. Events are marked with
    confidence='review'; the review UI's query picks the latest event per row, so
    the newest decision is current and any earlier one can be undone.
    """
    conn.execute(
        "INSERT INTO audit_log (run_id, row_index, sources_consulted, confidence, provider, human_action, timestamp) "
        "VALUES (?, ?, ?, 'review', 'review-ui', ?, ?)",
        (run_id, row_index, json.dumps({"review_event": True}), human_action, datetime.now(UTC).isoformat()),
    )
    if reviewed_answer is not None:
        conn.execute(
            "UPDATE answers SET reviewed_answer = ? WHERE run_id = ? AND row_index = ?",
            (reviewed_answer, run_id, row_index),
        )
    # C4: an approved or edited row with a real answer joins the answer library —
    # this is how the library compounds across questionnaires. The final text is the
    # drafted answer for "approved" and the human's text for "edited"; the snapshot
    # hashes of the cited source docs gate future freshness.
    if human_action in ("approved", "edited"):
        row = conn.execute(
            "SELECT question_text, drafted_answer, reviewed_answer, polarity, cited_chunk_ids, cited_sentences "
            "FROM answers WHERE run_id = ? AND row_index = ?",
            (run_id, row_index),
        ).fetchone()
        if row is not None:
            final_text = (reviewed_answer if human_action == "edited" else None) or row["drafted_answer"] or ""
            if final_text.strip():
                cited_ids = json.loads(row["cited_chunk_ids"]) if row["cited_chunk_ids"] else []
                cited_sentences = json.loads(row["cited_sentences"]) if row["cited_sentences"] else []
                hashes = {}
                if cited_ids:
                    placeholders = ",".join("?" * len(cited_ids))
                    files = [
                        r["source_filename"]
                        for r in conn.execute(
                            f"SELECT source_filename FROM chunks WHERE embedding_id IN ({placeholders})", cited_ids
                        )
                    ]
                    hashes = {f: h for f, h in current_source_hashes(conn).items() if f in files}
                store_reviewed_answer(
                    conn,
                    run_id,
                    row_index,
                    row["question_text"],
                    final_text,
                    row["polarity"],
                    cited_ids,
                    cited_sentences,
                    hashes,
                    human_action,
                )
    conn.commit()


def record_export(conn: sqlite3.Connection, run_id: int, output_path: str, review_counts: dict) -> None:
    """Record a reviewed-workbook export. No new table or column: reuses audit_log
    with row_index=-1, a sentinel no real question row ever has, since an export is a
    run-level event rather than something that happened to one row. sources_consulted
    (a JSON text column, already used for a different purpose on per-row entries)
    carries the exported path and the approved/edited/rejected counts at export time —
    "the review state at that moment," not just that an export happened.
    """
    conn.execute(
        "INSERT INTO audit_log (run_id, row_index, sources_consulted, confidence, provider, human_action, timestamp) "
        "VALUES (?, -1, ?, 'export', NULL, 'exported', ?)",
        (
            run_id,
            json.dumps({"output_path": output_path, "review_counts": review_counts}),
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
