"""SQLite schema and connection helper for chunk metadata, questionnaire runs, and the audit log."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path("out") / "store.db"

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
    cited_chunk_ids TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES questionnaire_runs(id),
    row_index INTEGER NOT NULL,
    sources_consulted TEXT,
    confidence TEXT NOT NULL,
    human_action TEXT,
    timestamp TEXT NOT NULL
);
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def start_questionnaire_run(conn: sqlite3.Connection, source_path: str, output_path: str) -> int:
    cursor = conn.execute(
        "INSERT INTO questionnaire_runs (source_path, output_path, created_at) VALUES (?, ?, ?)",
        (source_path, output_path, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


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
) -> None:
    conn.execute(
        "INSERT INTO answers (run_id, row_index, question_text, sub_question_text, drafted_answer, "
        "vocab_selection, self_confidence, final_confidence, cited_chunk_ids) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            row_index,
            question_text,
            sub_question_text,
            drafted_answer,
            vocab_selection,
            self_confidence,
            final_confidence,
            json.dumps(cited_chunk_ids),
        ),
    )
    conn.commit()


def record_audit_entry(
    conn: sqlite3.Connection,
    run_id: int,
    row_index: int,
    sources_consulted: list[str],
    confidence: str,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (run_id, row_index, sources_consulted, confidence, human_action, timestamp) "
        "VALUES (?, ?, ?, ?, NULL, ?)",
        (run_id, row_index, json.dumps(sources_consulted), confidence, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
