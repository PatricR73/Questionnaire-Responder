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
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr
