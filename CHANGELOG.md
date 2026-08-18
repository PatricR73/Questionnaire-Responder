# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — client-facing (pack 3)

- **`qresp demo`**: one command to a filled workbook plus a running review
  screen, no API key and no model download — backed by a committed pre-built
  store (`demo_store/`) and a `Dockerfile`. The image is published to
  `ghcr.io/patricr73/qresp-demo` (`latest` + version tag) by the release
  workflow on tagged pushes, so `docker run -p 8501:8501
  ghcr.io/patricr73/qresp-demo:latest` is the zero-setup path.
- **Hosted read-only review screen**: `QRESP_REVIEW_READ_ONLY` freezes the UI
  (no approve/edit/export, frozen-sample banner); `streamlit_app.py` deploys it
  to Streamlit Community Cloud over the committed synthetic demo store
  (`docs/HOSTED-DEMO.md`).
- **Answer library** (`answer_library` flag, default OFF): human-approved
  answers persist to a separate, freshness-gated namespace and are surfaced to
  the generator as labelled candidates with provenance — never as retrieval
  evidence; rows that reuse one are marked with a third fill colour.
- **`qresp gap-report`**: NOT_FOUND rows as a documentation gap analysis
  (Markdown + XLSX), grouped by domain, with the closest evidence actually
  retrieved per gap.
- **Multi-sheet questionnaires**: every question-bearing tab is processed
  (`--sheet NAME` targets one), `qresp inspect` shows the detection with
  scores before any spend, and `--map question=C,answer=E,vocab=D` overrides
  detection entirely.
- **`--provider local`**: fully on-premise generation via any OpenAI-compatible
  endpoint (Ollama/vLLM/llama.cpp) with the same citation and entailment
  guarantees; measured baseline published in `EVAL.md` (qwen2.5:0.5b: 9/24
  structural match, two abstention regressions).
- **Workspaces** (`--workspace NAME`, `qresp workspace list/new`): per-client
  data directories — isolation at the storage layer, structurally impossible to
  cross.
- **Integration surfaces**: `from qresp import Pipeline` (ingest/answer/
  gap_report) and a minimal FastAPI service (`qresp serve`) — submit, poll,
  fetch; `docs/INTEGRATION.md`.
- **Security posture**: `docs/SECURITY-POSTURE.md`, `qresp purge` (delete a
  workspace's store), optional SQLCipher at-rest encryption
  (`QRESP_STORE_KEY`), and a review-UI warning when the bind address is
  widened.
- **README** restructured buyer-first (problem → try it → what you get → what it
  costs → why trust it → how it works → results), with a measured ROI table
  (`docs/sample/roi_measurement.md`).
- **`docs/CASE-STUDY.md`** (1,600 words) and **`docs/ROADMAP-CONNECTORS.md`**
  (scoped, deliberately unbuilt; tracked as issue #15, milestone v0.3.0).

### Changed

- `answers` gains `sheet_name` and `library_candidate` columns; new
  `source_docs` and `reviewed_answers` tables; multi-sheet runs record the
  sheet per row.
- `run_eval.py` accepts `--provider` so the local path is measured by the same
  documented command.
- The v2 priority order in `docs/DESIGN.md` moves the answer library to #1 —
  explicitly a commercial reorder, stated as such.

- Live eval of the A1 entailment layer (flag on/off, --repeats 3) once API
  credits are available; the false-positive cost (correct answers wrongly killed)
  decides whether the flag stays on.
- Blind hand re-score of the 24-question set per the scoring protocol in
  `fixtures/eval/LABELING_GUIDE.md`; the published 15/24 is the structural match,
  the usable/needs-editing/wrong hand-score from the original 20 is superseded.

## [0.1.0] - 2026-08-17

First release. The eval numbers this tag anchors (15/24 structural match on the
24-question set) are measured, not aspirational — see the release notes and
`EVAL.md`.

### Added

- Eval reproducibility: deterministic generation (`temperature` handling,
  `--repeats N`, per-question stability, 95% Wilson CI in `run_eval.py`), polarity
  scoring with inversion regression lines, and an adversarial eval subset
  (ADV-01..04) plus a documented blind scoring protocol.
- A third confidence layer — the entailment (support) check
  (`src/answer/entailment.py`), behind the `entailment_check` flag: verifies the
  drafted answer's claims are STATED in the cited sentences, not merely plausible
  from them, with its token cost reported separately. Default OFF until its
  false-positive cost is measured.
- Local cross-encoder reranker (`BAAI/bge-reranker-base`) behind the
  `reranker` flag, carrying `rerank_score` on `RetrievedChunk`.
- `--dry-run` cost estimation with the real (local) Claude tokenizer, plus
  `--dry-run --exact` using the free count_tokens API; costs report an honest
  range (1.4-1.9x tokenizer undercount band) at two significant figures.
- Central `Config` dataclass (CLI flag > env > TOML > default) serialized into
  each run's `run_config` column with the git revision; one-line config
  fingerprint at run start.
- Structured logging: `--verbose`/`--quiet`, a JSON-lines log beside the output
  workbook with per-row retrieval scores, token counts, and full tracebacks.
- Prompt caching for the system prompt, cached/uncached token breakdown, and an
  estimated dollar cost in the run summary.
- Review UI: pagination, batched chunk loading, polarity/self-confidence badges,
  cited-sentence highlighting, `cited_sentences` persistence, reviewed-workbook
  export with error-row handling, and append-only undoable review events.
- CI (pytest from the lockfile and the loose file, ruff check + format, mypy),
  `pyproject.toml` with a `qresp` console entry point and a pinned dev extra,
  a committed `requirements.lock`, and a pinned embedding-model revision.
- Community files: `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`,
  Dependabot config, `CHANGELOG.md`, and a documented data-handling section in
  the README ("what leaves your machine").
- The no-fabrication claim rewritten as a design goal with a measured failure
  rate; the README split into README + `docs/DESIGN.md` with a Mermaid pipeline
  diagram; the demo converted GIF → WebM with a clickable poster.

### Fixed

- Stale-chunk retention on re-ingest (evidence-relative chunk keys,
  delete-before-insert) — the highest-severity bug in the repo's history.
- Citation grounding now per-chunk (a sentence stitched across a chunk boundary
  no longer passes), with the actually-cited chunk ids recorded.
- Vocab selection constrained by schema enum + runtime membership assert; stale
  vocabulary values cleared on NOT_FOUND/ERROR rows.
- Zero-score BM25 candidates dropped from RRF fusion; `max_tokens` truncation
  detected as `AnswerTruncatedError`; SDK double-retry removed, Retry-After
  honoured, fatal auth/request errors abort the run, consecutive-error circuit
  breaker; token accounting on failed rows.
- Chunks get a real size ceiling and sentence-aware overlap (no severed
  sentences).
- `evidence` unbound/stale on the pipeline error path; `ZeroDivisionError` in
  the token summary for all-NOT_FOUND runs; compound-question loop now aggregates
  into one row-level result (weakest confidence across parts).
- Data paths resolve against the repo root (`QRESP_DATA_DIR` override); the
  sidecar JSONL carries run_id + timestamp; `--output` matching
  `--questionnaire` is rejected.
- Column detection scores headers instead of first-match (CAIQ's "Control ID"
  decoys penalized); data-validation ranges matched with real range math
  (AC-vs-C regression), range references and quoted values supported.
- `ADV-02` label corrected on the evidence (was recorded as a fabrication; the
  evidence verbatim documents the claimed control) — the adversarial subset
  caught a labeler error, which is its job.
- CAIQ licensing verified against the instrument's own license clause (quote
  portions with attribution) instead of an asserted fair-use defense.

### Changed

- Evaluated baseline: 15/24 structural match on the 24-question set (the
  previously published 14/24 counted `ADV-02` as a regression before the label
  correction; 13/20 usable on the original 20 is a superseded hand-score).
- Chroma distance metric pinned to cosine; `WEAK_MATCH_DISTANCE` re-expressed in
  cosine space after an honest failed re-derivation (the distributions overlap;
  0.3 kept as the established boundary).
- bge query-side instruction prefix added; `requirements.txt` stays the loose
  declaration with `requirements.lock` as the reproducibility path.
