"""P16: --dry-run estimates cost without doing anything that costs money.

The README warns that a real questionnaire can be hundreds of rows and every row is
a paid API call, but offers no way to find out what a run will cost first. --dry-run
does everything up to the API call — column detection, question reading, retrieval
for every selected row — counts input tokens with the real (local) Claude tokenizer,
adds an output estimate from observed averages, and prints an estimated cost. It
must make zero API calls, write no workbook, and create no run row.
"""

import os

from click.testing import CliRunner

from src.pipeline import cli
from src.store import db

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures")
import pathlib
FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def test_dry_run_reports_cost_without_writing_anything(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))

    output = tmp_path / "out.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire", str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output", str(output),
            "--limit", "0",
            "--provider", "anthropic",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "rows detected:" in result.output
    assert "estimated input tokens:" in result.output
    assert "estimated output tokens:" in result.output
    assert "estimated cost:" in result.output

    # zero side effects on the output side
    assert not output.exists(), "dry-run must not write the workbook"
    assert not output.with_suffix(".jsonl").exists(), "dry-run must not write the sidecar log"

    # no run row is created in questionnaire_runs (the empty store may exist
    # because retrieval reads it, but nothing is recorded)
    store_path = pathlib.Path(tmp_path) / "data" / "store.db"
    if store_path.exists():
        conn = db.connect(store_path)
        assert conn.execute("SELECT COUNT(*) AS n FROM questionnaire_runs").fetchone()["n"] == 0


def test_dry_run_does_not_require_an_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    output = tmp_path / "out.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire", str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output", str(output),
            "--limit", "2",
            "--provider", "anthropic",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rows selected: 2" in result.output
