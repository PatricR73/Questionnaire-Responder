"""Regression test for a real bug: `streamlit run src/review_ui.py` failed with
ModuleNotFoundError because Streamlit only adds the script's own directory to
sys.path (see streamlit.web.bootstrap._fix_sys_path), not the project root, so
`from src.store import db` couldn't resolve.

A test that only checks the HTTP server boots (e.g. curling "/") does NOT catch this
class of bug — Streamlit serves the app shell over HTTP before ever executing the
script; the script only runs once a session actually connects. This test instead
replicates Streamlit's real sys.path setup exactly and executes the actual file in a
subprocess, which is the only way to catch it. cwd is deliberately not the project
root and PYTHONPATH is deliberately stripped, so the project root can't sneak onto
sys.path by some other route and mask the bug.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "src" / "review_ui.py"


def test_data_dir_resolves_against_repo_root_unless_overridden(monkeypatch):
    """Unit coverage for src/data_dir.py itself: the resolver must honour
    QRESP_DATA_DIR when set and fall back to the repo root (from __file__, not the
    cwd) otherwise — the two behaviours the review UI's data access depends on."""
    from src.data_dir import data_dir

    monkeypatch.delenv("QRESP_DATA_DIR", raising=False)
    assert data_dir() == REPO_ROOT / "out"

    monkeypatch.setenv("QRESP_DATA_DIR", "/tmp/custom_data_dir")
    assert data_dir() == Path("/tmp/custom_data_dir")


def test_review_ui_finds_a_seeded_run_from_a_foreign_cwd(tmp_path, monkeypatch):
    """Regression test for the cwd-relative data-dir bug: the review UI used to open
    Path("out")/"store.db" relative to the process cwd, so running it from anywhere
    but the repo root silently showed an empty database — and the old assertion
    (exit code 0) passed because the empty-DB path renders cleanly, actively masking
    the bug. The data dir now resolves against the repo root or QRESP_DATA_DIR, and
    this test proves it end to end: seed a run into a temp data dir, point
    QRESP_DATA_DIR at it, run the real script from a foreign cwd via Streamlit's
    AppTest (which provides the ScriptRunContext bare-mode execution lacks, so the
    rendered elements are actually observable), and assert the seeded run is found —
    not merely that the script exited cleanly.
    """
    from streamlit.testing.v1 import AppTest

    from src.store import db

    data_dir = tmp_path / "data"
    conn = db.connect(data_dir / "store.db")
    run_id = db.start_questionnaire_run(conn, "source.xlsx", "out.xlsx")
    db.record_answer(
        conn,
        run_id,
        2,
        "Question one?",
        "Question one?",
        "Drafted answer text",
        None,
        "high",
        "high",
        [],
    )
    conn.close()

    monkeypatch.setenv("QRESP_DATA_DIR", str(data_dir))
    monkeypatch.chdir(tmp_path)

    at = AppTest.from_file(str(SCRIPT)).run()

    assert not at.exception
    rendered = "\n".join(str(m.value) for m in at.markdown)
    assert "Drafted answer text" in rendered  # the seeded answer is actually rendered
    assert "No questionnaire runs found" not in rendered  # not the empty-DB path
    # the sidebar run picker lists the seeded run (label includes its source path)
    assert at.sidebar.selectbox[0].options == ["source.xlsx  —  " + _created_at_label(data_dir)]


def test_review_ui_shows_polarity_downgrade_and_cited_sentences(tmp_path, monkeypatch):
    """P28: the review screen must show what the model actually claimed — polarity
    as a labeled badge, self_confidence next to final_confidence when the
    cross-check downgraded the model, and the cited sentences highlighted within
    the chunk text."""
    from streamlit.testing.v1 import AppTest

    from src.review_ui import _highlight_cited
    from src.store import db

    data_dir = tmp_path / "data"
    conn = db.connect(data_dir / "store.db")
    run_id = db.start_questionnaire_run(conn, "source.xlsx", "out.xlsx")
    conn.execute(
        "INSERT INTO chunks (source_filename, heading_path, loc_ref, text, embedding_id) "
        "VALUES ('policy.md', 'Encryption', 'line 3', ?, 'doc.md::0')",
        ("All traffic is encrypted in transit using TLS 1.2. Keys rotate quarterly.",),
    )
    conn.commit()
    db.record_answer(
        conn,
        run_id,
        2,
        "Question one?",
        "Question one?",
        "Drafted answer text",
        None,
        "high",
        "low",
        ["doc.md::0"],
        polarity="partial",
        cited_sentences=["All traffic is encrypted in transit using TLS 1.2."],
    )
    conn.close()

    monkeypatch.setenv("QRESP_DATA_DIR", str(data_dir))
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(str(SCRIPT)).run()

    assert not at.exception
    rendered = "\n".join(str(m.value) for m in at.markdown)
    assert "PARTIAL" in rendered  # polarity badge is shown
    assert "model said: high" in rendered  # downgrade (high -> low) is visible
    assert "**All traffic is encrypted in transit using TLS 1.2.**" in rendered  # cited sentence highlighted
    assert _highlight_cited("X. Cited sentence Y. Z.", ["Cited sentence Y."]) == "X. **Cited sentence Y.** Z."


def _created_at_label(data_dir: Path) -> str:
    """Re-read the seeded run's created_at to build the exact selectbox label the
    review UI produces (f"{source_path}  —  {created_at}")."""
    from src.store import db

    conn = db.connect(data_dir / "store.db")
    row = conn.execute("SELECT created_at FROM questionnaire_runs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row["created_at"]


def test_review_ui_runs_under_streamlits_actual_sys_path_setup(tmp_path):
    code = (
        "import runpy\n"
        "import streamlit.web.bootstrap as bootstrap\n"
        f"bootstrap._fix_sys_path({str(SCRIPT)!r})\n"
        f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
