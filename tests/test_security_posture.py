"""Security-posture tests (pack 3, C11): at-rest encryption and purge.

The posture document promises two shipped mitigations: optional SQLCipher at-rest
encryption of the store, and a purge command that actually removes the store. Both
are pinned here so the document stays true.
"""

import sqlite3
from pathlib import Path

import pytest
import sqlcipher3

from src.store import db

# Both connection backends raise their own DatabaseError classes (sqlcipher3's
# exceptions are NOT sqlite3 subclasses); a wrong-key open must fail with one of
# them, never with a MemoryError or a silent read.
DB_ERROR = (sqlite3.DatabaseError, sqlcipher3.dbapi2.DatabaseError)


def test_encrypted_store_unreadable_without_key(tmp_path):
    path = tmp_path / "enc.db"
    conn = db.connect(Path(path), key="correct-key-123")
    conn.execute("INSERT INTO questionnaire_runs (source_path, output_path, created_at) VALUES (?, ?, ?)", ("a", "b", "t"))
    conn.commit()
    conn.close()

    # Plain sqlite3 cannot read it.
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("SELECT COUNT(*) FROM questionnaire_runs")
    # Wrong key cannot read it.
    with pytest.raises(DB_ERROR):
        wrong = db.connect(Path(path), key="wrong-key")
        wrong.execute("SELECT COUNT(*) FROM questionnaire_runs")
    # Correct key reads it.
    right = db.connect(Path(path), key="correct-key-123")
    assert right.execute("SELECT COUNT(*) FROM questionnaire_runs").fetchone()[0] == 1


def test_store_key_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("QRESP_STORE_KEY", "env-key-456")
    path = tmp_path / "env.db"
    conn = db.connect(Path(path))
    conn.execute("INSERT INTO questionnaire_runs (source_path, output_path, created_at) VALUES (?, ?, ?)", ("a", "b", "t"))
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("SELECT COUNT(*) FROM questionnaire_runs")


def test_purge_removes_store(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from src.pipeline import cli

    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "ws"))
    db.connect().execute("CREATE TABLE IF NOT EXISTS t(x)")  # ensure store exists
    store = tmp_path / "ws" / "store.db"
    assert store.exists()
    result = CliRunner().invoke(cli, ["purge", "--yes"])
    assert result.exit_code == 0, result.output
    assert not store.exists()
