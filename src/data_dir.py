"""Single resolver for where run data lives on disk.

Everything the pipeline persists — the SQLite store (out/store.db), the Chroma index
(out/chroma/) — lives under one data directory. Historically that was Path("out")
resolved against the current working directory, which made
`streamlit run src/review_ui.py` from any directory other than the repo root
silently open an empty database and report "No questionnaire runs found" (and made
the entrypoint test pass for the wrong reason: the empty-DB path renders cleanly, so
a foreign cwd was never caught).

The directory now resolves against the repo root, derived from this file's __file__
so it works regardless of cwd, and can be overridden wholesale with the
QRESP_DATA_DIR environment variable — which is also what lets tests point a run at
an isolated temp data dir and assert the run is actually found.

Deliberately not cached: the resolver is read at call time so an env-var change
(e.g. monkeypatch.setenv in a test, or a wrapper script) takes effect for the next
connection, and the cost is one os.environ lookup per connect — negligible next to
opening SQLite or starting Chroma.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("QRESP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "out"
