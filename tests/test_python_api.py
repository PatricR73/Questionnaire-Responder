"""Tests for the stable Python API and the integration service (pack 3, C10).

The API must run the exact CLI code path in-process (no shelling out, no
re-implemented logic), and the service must be a thin HTTP wrapper over the same
surface — submit, poll, fetch.
"""

import time
from pathlib import Path

from qresp import Pipeline

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_pipeline_ingest_answer_gap(tmp_path):
    pipeline = Pipeline(data_dir=tmp_path / "data")
    n = pipeline.ingest(FIXTURES / "evidence")
    assert n > 0

    result = pipeline.answer(
        FIXTURES / "eval" / "questionnaire_eval.xlsx",
        tmp_path / "filled.xlsx",
        provider="stub",
        limit=3,
    )
    assert result.run_id > 0
    assert len(result.rows) == 3
    assert all(r.provider == "stub" for r in result.rows)
    assert sum(result.counts.values()) == 3

    report = pipeline.gap_report(result.run_id)
    assert report.gap_count >= 0
    assert report.total_questions == 3
    # Rendering to disk works too.
    gap_dir = tmp_path / "gaps"
    pipeline.gap_report(result.run_id, output_dir=gap_dir)
    assert (gap_dir / f"gap_report_run{result.run_id}.md").exists()


def test_pipeline_dry_run_returns_no_run(tmp_path):
    pipeline = Pipeline(data_dir=tmp_path / "data2")
    pipeline.ingest(FIXTURES / "evidence")
    result = pipeline.answer(
        FIXTURES / "eval" / "questionnaire_eval.xlsx",
        tmp_path / "dry.xlsx",
        provider="stub",
        limit=2,
        dry_run=True,
    )
    assert result.run_id == 0
    assert result.rows == []


def test_service_submit_poll_results(tmp_path):
    from fastapi.testclient import TestClient

    from src.service import app

    data_dir = tmp_path / "svc"
    client = TestClient(app)
    resp = client.post(
        "/runs",
        json={
            "questionnaire": str(FIXTURES / "eval" / "questionnaire_eval.xlsx"),
            "evidence_dir": str(FIXTURES / "evidence"),
            "provider": "stub",
            "limit": 2,
            "data_dir": str(data_dir),
        },
    )
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]

    for _ in range(120):
        status = client.get(f"/runs/{run_id}").json()["status"]
        if status in ("done", "error"):
            break
        time.sleep(0.5)
    assert status == "done", f"run never finished: {client.get('/runs/' + run_id).json()}"

    results = client.get(f"/runs/{run_id}/results").json()
    assert results["run_id"] > 0
    assert len(results["rows"]) == 2

    # Unknown run -> 404.
    assert client.get("/runs/doesnotexist").status_code == 404
