"""Wave B regression tests for the pipeline loop.

B1: `evidence` is unbound/stale on the per-row error path.
B2: `ZeroDivisionError` in the token summary when nothing was answered.
B3: the compound-question loop aggregates into ONE row-level result.
"""

import json
import pathlib

from click.testing import CliRunner

import src.pipeline as pipeline_module
from src.answer.answerer import AnswerResult, AnswerStatus
from src.pipeline import cli
from src.retrieval.hybrid_search import HybridSearcher
from src.store import db

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE = FIXTURES / "questionnaire_sample.xlsx"
EVIDENCE = FIXTURES / "evidence"


def _ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    result = CliRunner().invoke(cli, ["ingest", "--evidence-dir", str(EVIDENCE)])
    assert result.exit_code == 0, result.output
    return tmp_path / "out.xlsx"


def test_b1_search_failure_on_first_row_is_a_recoverable_error(tmp_path, monkeypatch):
    """If searcher.search raises on the FIRST row, the row must be recorded as
    ERROR with an empty retrieval list and the run must complete — the old code
    died with NameError inside the error handler because `evidence` was never
    bound."""
    output = _ingest(tmp_path, monkeypatch)

    class FirstCallFailingSearcher(HybridSearcher):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._fail_first = True

        def search(self, *a, **k):
            if self._fail_first:
                self._fail_first = False
                raise RuntimeError("simulated search failure on first row")
            return super().search(*a, **k)

    monkeypatch.setattr(pipeline_module, "HybridSearcher", FirstCallFailingSearcher)
    result = CliRunner().invoke(
        cli,
        ["answer", "--questionnaire", str(SAMPLE), "--output", str(output), "--limit", "0", "--provider", "stub"],
    )
    assert result.exit_code == 0, result.output

    conn = db.connect(pathlib.Path(tmp_path) / "data" / "store.db")
    # every row processed; the first one is an ERROR
    assert conn.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"] == 6
    first = conn.execute("SELECT final_confidence FROM answers ORDER BY row_index LIMIT 1").fetchone()
    assert first["final_confidence"] == "error"

    records = [json.loads(line) for line in output.with_suffix(".log.jsonl").read_text().splitlines()]
    failed = next(r for r in records if r.get("final_confidence") == "error")
    assert failed["retrieval"] == []  # empty, not stale from another row


def test_b2_all_not_found_run_does_not_divide_by_zero(tmp_path, monkeypatch):
    """A run where every row abstains still spends real tokens; the summary must
    print the totals and not crash on a zero answered count."""
    output = _ingest(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class AllNotFound:
        provider_name = "anthropic"

        def __init__(self, config=None, **kwargs):
            pass

        def answer_question(self, *a, **k):
            return AnswerResult(
                answer="",
                status=AnswerStatus.NOT_FOUND,
                confidence=None,
                cited_chunk_ids=[],
                provider="anthropic",
                input_tokens=100,
                output_tokens=50,
            )

    monkeypatch.setattr(pipeline_module, "AnthropicAnswerer", AllNotFound)
    result = CliRunner().invoke(
        cli,
        ["answer", "--questionnaire", str(SAMPLE), "--output", str(output), "--limit", "0", "--provider", "anthropic"],
    )
    assert result.exit_code == 0, result.output
    assert "no answered questions" in result.output
    assert "Tokens: 600 in / 300 out" in result.output  # totals still reported


def test_b3_two_part_splitter_aggregates_to_one_row(tmp_path, monkeypatch):
    """With a real two-part splitter, one sheet row must produce exactly one cell
    write, one answers row, one audit entry, and one count — with the WEAKEST
    confidence winning across parts (an unsupported part makes the row NOT_FOUND)."""
    output = _ingest(tmp_path, monkeypatch)
    monkeypatch.setattr(
        pipeline_module,
        "split_question",
        lambda text: ["Is data encrypted in transit?", "Do you maintain a public bug bounty program?"],
    )
    result = CliRunner().invoke(
        cli,
        ["answer", "--questionnaire", str(SAMPLE), "--output", str(output), "--limit", "0", "--provider", "stub"],
    )
    assert result.exit_code == 0, result.output

    conn = db.connect(pathlib.Path(tmp_path) / "data" / "store.db")
    n_rows = conn.execute("SELECT COUNT(*) AS n FROM answers").fetchone()["n"]
    assert n_rows == 6, f"one answers row per sheet row, got {n_rows}"
    n_audit = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    assert n_audit == 6, f"one audit entry per sheet row, got {n_audit}"

    # weakest confidence: every row has one answerable part and one unanswerable
    # part, so every row aggregates to NOT_FOUND
    assert conn.execute("SELECT COUNT(*) AS n FROM answers WHERE final_confidence = 'none'").fetchone()["n"] == 6
    row = conn.execute("SELECT drafted_answer, sub_question_text FROM answers ORDER BY row_index LIMIT 1").fetchone()
    assert "Is data encrypted in transit?" in row["sub_question_text"]
    assert "Do you maintain a public bug bounty program?" in row["sub_question_text"]
    # the combined answer text carries both parts with their lead-ins
    assert "Is data encrypted in transit?" in (row["drafted_answer"] or "")
    assert "Do you maintain a public bug bounty program?" in (row["drafted_answer"] or "")

    # the workbook cell shows the NOT_FOUND marker (single cell, not a raw answer)
    import openpyxl

    from src.questionnaire.write_xlsx import NOT_FOUND_MARKER

    ws = openpyxl.load_workbook(output).active
    for row_index in (3, 5, 6, 8, 9, 10):
        assert ws.cell(row=row_index, column=3).value == NOT_FOUND_MARKER, f"row {row_index} cell"

    # the run summary counted each sheet row once, not once per part
    assert "answered=0" in result.output
    assert "not_found=6" in result.output
