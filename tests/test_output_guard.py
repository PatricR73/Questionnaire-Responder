"""P20: sidecar records carry a run id, and --output can never clobber the source."""

import json
import pathlib

from click.testing import CliRunner

from src.pipeline import cli

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"


def test_jsonl_records_carry_run_id_and_timestamp(tmp_path, monkeypatch):
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
            "stub",
        ],
    )
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in output.with_suffix(".jsonl").read_text().splitlines()]
    assert records, "sidecar must have records"
    run_ids = {r["run_id"] for r in records}
    assert len(run_ids) == 1, "all records in one run share a run_id"
    assert all(r["ts"] for r in records), "every record carries an ISO timestamp"


def test_output_matching_questionnaire_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    source = FIXTURES / "questionnaire_sample.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire",
            str(source),
            "--output",
            str(source),  # same file
            "--limit",
            "1",
            "--provider",
            "stub",
        ],
    )
    assert result.exit_code != 0
    assert "same file" in result.output


def test_output_symlink_to_questionnaire_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    source = FIXTURES / "questionnaire_sample.xlsx"
    link = tmp_path / "out.xlsx"
    link.symlink_to(source)
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire",
            str(source),
            "--output",
            str(link),  # symlink resolving to the source
            "--limit",
            "1",
            "--provider",
            "stub",
        ],
    )
    assert result.exit_code != 0
    assert "same file" in result.output
