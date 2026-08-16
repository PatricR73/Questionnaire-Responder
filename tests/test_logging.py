"""P19: structured logging.

The pipeline's only output channel used to be click.echo, and a failed row printed
str(exc) with no traceback — debugging one bad row out of 400 meant re-running with
--only-row and hoping it reproduced. A JSON-lines log file now sits next to the
output workbook capturing per-row timing, retrieval scores, token counts, and the
full exception for failed rows; --verbose exposes the same detail on stderr at
DEBUG, --quiet suppresses INFO progress.
"""

import json

from click.testing import CliRunner

from src.pipeline import cli

FIXTURES = __import__("pathlib").Path(__file__).resolve().parent.parent / "fixtures"
import pathlib


def test_structured_log_file_captures_rows_and_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    output = tmp_path / "out.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire", str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output", str(output),
            "--limit", "0",
            "--provider", "stub",
            "--stub-fail-row", "5",
        ],
    )
    assert result.exit_code == 0, result.output

    log_path = output.with_suffix(".log.jsonl")
    assert log_path.exists()
    records = [json.loads(line) for line in log_path.read_text().splitlines()]

    # every processed row is a structured record (run-level INFO lines like "answer
    # run starting" carry no row_data and are filtered out)
    row_records = [r for r in records if "row_index" in r]
    assert len(row_records) >= 1
    assert all("elapsed_seconds" in r and "retrieval" in r for r in row_records)
    assert all("input_tokens" in r and "output_tokens" in r for r in row_records)

    # the failed row carries the full exception
    failed = [r for r in records if r.get("final_confidence") == "error"]
    assert failed, "stub-fail-row must produce an error record"
    assert "traceback" in failed[0]
    assert "simulated failure" in failed[0]["traceback"]
    assert "error" in failed[0]


def test_verbose_shows_detail_quiet_hides_it(tmp_path, monkeypatch):
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    args = [
        "answer",
        "--questionnaire", str(FIXTURES / "questionnaire_sample.xlsx"),
        "--output", str(tmp_path / "v.xlsx"),
        "--limit", "1",
        "--provider", "stub",
    ]
    quiet = CliRunner().invoke(cli, args + ["--quiet"])
    verbose = CliRunner().invoke(cli, args + ["--verbose"])
    assert quiet.exit_code == 0
    assert verbose.exit_code == 0
    # --quiet suppresses INFO-level run milestones; --verbose shows per-row DEBUG lines
    assert "answer run starting" not in quiet.output
    assert "answer run starting" in verbose.output
