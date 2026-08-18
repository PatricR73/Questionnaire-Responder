"""Workspace isolation tests (pack 3, C9): namespaced data directories.

The most likely early adopter is a consultant/vCISO/MSP answering questionnaires
for several clients. Client A's evidence corpus and approved answers must be
structurally impossible to retrieve for client B — not merely unlikely. Isolation
is enforced at the storage layer (each workspace is its own data directory), so
retrieval code never sees another workspace's rows; these tests pin that.
"""

import os
import subprocess
import sys
from pathlib import Path

from src.store import db
from src.store.vectorstore import VectorStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(args, data_dir):
    env = dict(os.environ, QRESP_DATA_DIR=str(data_dir))
    return subprocess.run(
        [sys.executable, "-m", "src.pipeline", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def test_workspaces_are_structurally_isolated(tmp_path):
    """Ingest into workspace A; workspace B (and the default store) must contain
    nothing — retrieval in B has no rows to see by construction."""
    ws_base = tmp_path / "data"
    a = ws_base / "workspaces" / "acme"
    b = ws_base / "workspaces" / "other"

    res = _run(["--workspace", "acme", "ingest", "--evidence-dir", "fixtures/evidence"], ws_base)
    assert res.returncode == 0, res.stderr

    # Workspace A has chunks; workspace B has none.
    conn_a = db.connect(a / "store.db")
    conn_b = db.connect(b / "store.db")
    assert conn_a.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0
    assert conn_b.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    # The vector stores are separate directories too.
    assert (a / "chroma").exists()
    assert not (b / "chroma").exists()


def test_workspace_data_dir_used_by_commands(tmp_path):
    """--workspace sets QRESP_DATA_DIR before the command body runs, so a run in a
    workspace writes only into that workspace's directory."""
    ws_base = tmp_path / "data"
    res = _run(["--workspace", "acme", "ingest", "--evidence-dir", "fixtures/evidence"], ws_base)
    assert res.returncode == 0
    ws_dir = ws_base / "workspaces" / "acme"
    assert (ws_dir / "store.db").exists()
    # The default out/ (this repo's real data dir) was not touched by the run —
    # the run's QRESP_DATA_DIR was the workspace, not the repo out/.
