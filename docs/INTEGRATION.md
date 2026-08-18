# Integration guide

Pack 3, C10. This document answers the question a platform team actually asks —
"can we integrate this?" — with a link instead of a maybe. Two supported surfaces,
both thin over the same pipeline code:

1. **A stable Python API** (`from qresp import Pipeline`) for embedding this tool
   in a compliance portal, an intake flow, or an internal toolchain.
2. **A minimal HTTP service** (`qresp serve`) for the same three operations over
   the wire: submit a questionnaire run, poll status, fetch results.

Neither surface re-implements pipeline logic; both run the exact code path the CLI
runs, so behaviour, guarantees, and artifacts are identical. The CLI is a thin
wrapper over the same functions.

## 1. The Python API

```python
from qresp import Pipeline

pipeline = Pipeline()                     # optional: data_dir=..., config_file=...
pipeline.ingest("path/to/evidence/")      # parse + chunk + embed (idempotent)

result = pipeline.answer(
    "questionnaire.xlsx",
    "filled.xlsx",
    provider="stub",                      # "anthropic" (needs ANTHROPIC_API_KEY) | "openai-compatible"
    limit=0,                              # 0 = every question row
    sheet="Security Questionnaire",       # optional: one tab of a multi-sheet workbook
)
print(result.run_id, result.counts)       # {'high': 13, 'low': 0, 'none': 7, 'error': 0}
for row in result.rows:
    print(row.row_index, row.final_confidence, row.answer, row.cited_chunk_ids)

report = pipeline.gap_report(result.run_id, output_dir="reports/")
print(report.gap_count, report.domains_ranked)
```

- `ingest(evidence_dir) -> int` — chunk count.
- `answer(questionnaire, output, *, limit, provider, sheet, map_override, top_k, dry_run) -> AnswerRunResult`
  — the same flags as `qresp answer`; returns structured rows with the confidence
  state, polarity, cited chunk ids, and answer-library provenance per row.
- `gap_report(run_id, output_dir=None) -> GapReport` — the documentation gap
  analysis; optionally renders the .md/.xlsx artifacts.

The API shares the store with the CLI (same default data directory, same
`QRESP_DATA_DIR` override), so a questionnaire answered via the API is reviewed in
the same review UI. All the guarantees are identical: per-row fault isolation,
verbatim citation grounding, the optional entailment check, the answer library
behind its flag, workspaces.

## 2. The HTTP service

```
qresp serve --port 8000
```

Three endpoints, the minimum an intake flow needs:

```bash
# submit a run (background thread; provider defaults to stub so the API is
# tryable without a key)
curl -X POST localhost:8000/runs -H 'Content-Type: application/json' \
  -d '{"questionnaire": "fixtures/eval/questionnaire_eval.xlsx",
       "evidence_dir": "fixtures/evidence", "provider": "stub", "limit": 5}'
# -> {"run_id": "a1b2c3d4e5f6", "status": "running"}

# poll
curl localhost:8000/runs/a1b2c3d4e5f6
# -> {"run_id": "...", "status": "done"}

# fetch structured results
curl localhost:8000/runs/a1b2c3d4e5f6/results
```

The request body mirrors the API: `questionnaire`, `evidence_dir`, `provider`,
`limit`, `sheet`, `map_override`, `output`, `data_dir`.

### Service boundaries (read before deploying)

- **Single-worker, in-memory state.** Runs execute in background threads; a
  restart loses in-flight runs. The store is file-based (SQLite + Chroma on disk),
  and concurrent runs to the same store are not serialized — run one questionnaire
  at a time per store.
- **No authentication.** Bind to localhost (the default) and put a real
  authenticating reverse proxy in front for anything beyond a trusted network. The
  store contains internal policy text; treat the service as you would any system
  holding that data.
- **This is an integration surface, not a multi-tenant product.** It exists so a
  platform team can answer "can we integrate this?" with a link. Multi-tenant
  isolation, job queues, and auth are deliberate non-goals here — the workspace
  machinery (pack 3, C9) is the isolation primitive if you need per-client
  separation, and it works through the API (`data_dir` parameter).
- **Docker:** the C1 image installs this package; run the service in it with
  `docker run -p 8000:8000 <image> qresp serve --host 0.0.0.0`.

## 3. What is deliberately not in this surface

- No streaming, no webhooks, no cancellation. Submit → poll → fetch is the whole
  contract; anything richer belongs behind your own orchestration layer.
- No write access to the review store (approve/edit). Review remains a human step
  in the review UI — by design, not by omission: every output requires human
  review before it goes to a customer.
