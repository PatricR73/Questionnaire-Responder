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
            "--questionnaire",
            str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output",
            str(output),
            "--limit",
            "0",
            "--provider",
            "anthropic",
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
            "--questionnaire",
            str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output",
            str(output),
            "--limit",
            "2",
            "--provider",
            "anthropic",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rows selected: 2" in result.output


def test_dry_run_top_k_changes_the_estimate(tmp_path, monkeypatch):
    """B4: --dry-run must estimate the run that would actually happen — the
    resolved top_k (and other config) threads through, not a hardcoded 5."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    ingest = CliRunner().invoke(cli, ["ingest", "--evidence-dir", str(FIXTURES / "evidence")])
    assert ingest.exit_code == 0, ingest.output

    def run(top_k):
        output = tmp_path / f"out_{top_k}.xlsx"
        result = CliRunner().invoke(
            cli,
            [
                "answer",
                "--questionnaire",
                str(FIXTURES / "questionnaire_sample.xlsx"),
                "--output",
                str(output),
                "--limit",
                "0",
                "--provider",
                "anthropic",
                "--dry-run",
                "--top-k",
                str(top_k),
            ],
        )
        assert result.exit_code == 0, result.output
        line = next(l for l in result.output.splitlines() if "estimated input tokens" in l)
        return int(line.split("estimated input tokens:")[1].split("(")[0].strip())

    tokens_3 = run(3)
    tokens_12 = run(12)
    assert tokens_12 > tokens_3, f"top_k=12 ({tokens_12}) must exceed top_k=3 ({tokens_3}) when chunks exist"


def test_dry_run_reports_cost_as_a_range(tmp_path, monkeypatch):
    """B5: the local tokenizer undercounts by 1.4-1.9x, so the cost prints as a
    RANGE with two significant figures, not a false-precision point estimate."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    output = tmp_path / "out.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire",
            str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output",
            str(output),
            "--limit",
            "0",
            "--provider",
            "anthropic",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    line = next(l for l in result.output.splitlines() if "estimated cost" in l)
    assert "$" in line
    assert "-" in line, f"expected a range, got: {line}"
    assert "under-count band" in line or "undercount band" in line
    # two significant figures, not four decimals
    assert "0.0000" not in line


def test_dry_run_exact_requires_an_api_key(tmp_path, monkeypatch):
    """B5: --dry-run --exact calls the free count_tokens API, so it needs a key
    and must say so clearly instead of failing obscurely."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    output = tmp_path / "out.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire",
            str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output",
            str(output),
            "--limit",
            "2",
            "--provider",
            "anthropic",
            "--dry-run",
            "--exact",
        ],
    )
    assert result.exit_code != 0
    assert "count_tokens" in result.output
    assert "ANTHROPIC_API_KEY" in result.output
