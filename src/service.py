"""Minimal integration HTTP service (pack 3, C10): submit a run, poll, fetch results.

A buyer whose answer is "we'd want this in our compliance portal" or "our platform
team would wire this into our intake flow" needs a link, not a maybe. This service
is a thin HTTP wrapper over the qresp.Pipeline surface — it does not re-implement
any pipeline logic — exposing exactly the three operations an intake flow needs:
submit a questionnaire run, poll its status, fetch its structured results.

Deliberately minimal and single-worker by design (the store is file-based and a
run writes artifacts to disk):
- Each run executes in a background thread; status lives in an in-memory dict.
  Restarting the process loses in-flight runs (they are not queued anywhere).
- No auth is built in. Bind to localhost (the default) and put a real
  authenticating proxy in front for anything beyond a trusted network — the README
  and docs/INTEGRATION.md say so explicitly.
- Not a multi-tenant product: it is an integration surface to answer "can we
  integrate this?" — see docs/INTEGRATION.md for the boundary.

Run with: qresp serve --port 8000   (or: uvicorn src.service:app)
"""

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from qresp import Pipeline

app = FastAPI(title="Questionnaire Responder", version="0.1.0", description="Submit a questionnaire run, poll status, fetch results.")

# run_id -> {"status": "running"|"done"|"error", "output": str, "result": dict, "error": str}
_RUNS: dict[str, dict] = {}
_LOCK = threading.Lock()


class RunRequest(BaseModel):
    questionnaire: str
    output: str | None = None
    evidence_dir: str | None = None
    provider: str = "stub"
    limit: int = 0
    sheet: str | None = None
    map_override: str | None = None
    data_dir: str | None = None


def _execute(run_id: str, req: RunRequest) -> None:
    try:
        pipeline = Pipeline(data_dir=req.data_dir)
        if req.evidence_dir:
            pipeline.ingest(req.evidence_dir)
        output = req.output or f"out/service/{run_id}.xlsx"
        result = pipeline.answer(
            req.questionnaire, output, limit=req.limit, provider=req.provider,
            sheet=req.sheet, map_override=req.map_override,
        )
        with _LOCK:
            _RUNS[run_id] = {"status": "done", "output": output, "result": result.to_dict()}
    except Exception as exc:  # noqa: BLE001 — a failed run is reported, not a crashed worker
        with _LOCK:
            _RUNS[run_id] = {"status": "error", "error": str(exc)}


@app.post("/runs", status_code=201)
def submit(req: RunRequest) -> dict:
    """Submit a questionnaire run. Returns a run_id to poll; the run executes in
    the background. provider defaults to 'stub' (no API key needed) — pass
    provider='anthropic' with ANTHROPIC_API_KEY set, or provider='local' for a
    fully on-premise run."""
    run_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _RUNS[run_id] = {"status": "running"}
    thread = threading.Thread(target=_execute, args=(run_id, req), daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "running"}


@app.get("/runs/{run_id}")
def get_status(run_id: str) -> dict:
    with _LOCK:
        run = _RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    return {"run_id": run_id, "status": run["status"], "error": run.get("error")}


@app.get("/runs/{run_id}/results")
def get_results(run_id: str) -> dict:
    with _LOCK:
        run = _RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    if run["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Run not finished (status={run['status']})")
    return run["result"]
