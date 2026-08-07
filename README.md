# Vendor Security Questionnaire Responder

Automates first-draft answers to vendor security questionnaires by retrieving relevant
passages from an organization's own evidence base (security policies, access control
procedures, business continuity plans, prior questionnaire answers, etc.) and drafting
an answer strictly from that retrieved text.

This is slice 1 of the project: a CLI pipeline. There is no web UI, no review
interface, and no write-back of human-approved answers into the evidence base yet —
those are later slices.

## Non-goal: this tool does not fabricate answers

**The core guarantee is that it will never assert a security control the organization
has not documented.** If the evidence base doesn't support an answer, the tool does
not guess, does not extrapolate from "typical" industry practice, and does not fill
the gap with a plausible-sounding claim. That row is left with the literal marker
`NOT FOUND IN PROVIDED DOCUMENTS` instead.

This is enforced twice, independently:
1. The system prompt in `src/answer/generate.py` instructs Claude that no-evidence
   questions must return an empty, unsupported answer — this is documented as the
   *expected*, correct output for many questions, not a failure.
2. `src/answer/confidence.py` does not trust that instruction blindly: it checks that
   every sentence Claude cites as support actually appears verbatim in the evidence
   text that was retrieved. A citation that doesn't check out forces the answer back
   down to "not found," regardless of what Claude claims about itself.

**Every output requires human review before it goes to a customer.** High-confidence
answers are written directly into the workbook, but "high confidence" describes the
retrieval and citation-grounding checks that produced the draft — it is not a
substitute for a person reading the answer. Low-confidence rows are flagged visibly in
the workbook (yellow fill + comment) specifically to be reviewed. Rows that errored
during processing (see below) are flagged in red and must be re-run, not trusted as-is.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The first `ingest` or `answer` run downloads a local sentence-transformers embedding
model (a few hundred MB). This can take a few minutes and looks like a hang — it isn't.

## Usage

### 1. Ingest your evidence base

```
python -m src.pipeline ingest --evidence-dir path/to/your/evidence/
```

Parses every `.md`, `.txt`, `.docx`, and `.pdf` file in the directory (PDF parsing
exists but is not yet validated against a real fixture — see Known limitations),
splits each into sections anchored to their original headings, embeds the sections
locally, and stores them in a local Chroma vector index plus a SQLite metadata store
(`out/chroma/`, `out/store.db`).

### 2. Answer a questionnaire

```
python -m src.pipeline answer \
  --questionnaire path/to/questionnaire.xlsx \
  --output out/filled.xlsx \
  --limit 5
```

Detects the question/answer/constrained-vocabulary columns, answers each question
against the ingested evidence, and writes a filled copy of the workbook — merged
cells, section-header rows, blank spacer rows, and formatting are preserved; only
answer and vocabulary cells are touched.

Useful flags:
- `--limit N` — process at most N question rows (default 5). Use `--limit 0` to
  process every question in the sheet. Start small; a real questionnaire can be
  hundreds of rows and every row is a paid API call.
- `--only-row N` — process a single specific sheet row. Useful for targeted checks
  (e.g. re-running one flagged row, or exercising a specific abstention case).
- `--provider {anthropic,stub}` — see below.
- `--stub-fail-row N` — with `--provider stub`, forces that row to raise an error, to
  exercise the per-row failure-isolation path without spending real API calls.

Results are saved incrementally (every 5 rows, and always at the end) and every
processed row is appended to a sidecar `.jsonl` file next to the output workbook. A
crash partway through a run does not discard already-paid-for answers.

### Running without an API key: `--provider stub`

```
python -m src.pipeline answer --questionnaire fixtures/questionnaire_sample.xlsx \
  --output out/filled_stub.xlsx --limit 0 --provider stub
```

`--provider stub` exercises the entire pipeline — retrieval, column detection,
write-back, error isolation — without calling Claude or spending any money. It uses
the same retrieval-strength threshold the real path uses to decide whether it can
answer, so it's a meaningful test of the plumbing, not just a happy-path mock. Stub
output is stamped in the audit log and gets a visible red banner row in the output
workbook so it can never be mistaken for a real run's output.

`--provider anthropic` (the default) requires `ANTHROPIC_API_KEY` to be set and never
silently falls back to the stub if the key is missing — it errors instead, since every
row would otherwise fail identically.

### Supplying `ANTHROPIC_API_KEY`

Don't let an agent or script set this for you inside a session where it will end up in
scrollback or a generated file. Export it yourself:

```
export ANTHROPIC_API_KEY=sk-ant-...
```

or put it in a local `.env` file (already covered by `.gitignore` — never commit it)
and load it before running, e.g. `set -a; source .env; set +a`.

## Known limitations (slice 1)

- **PDF parsing is unvalidated.** `src/ingest/parse_docs.py` can parse PDFs, but
  heading detection there is a font-size/layout heuristic with no PDF fixture tested
  against it yet, and it assumes a born-digital PDF (real text, not a scanned image).
  Don't trust it on a real PDF without checking the resulting chunks first.
- **Compound question splitting is a stub.** Multi-part questions ("Do you do X, and
  how often is Y?") are answered as a single question rather than split into parts.
- **No review UI.** Review means opening the output workbook directly.
- **No feedback loop.** Human-approved/edited answers are not written back into the
  evidence base yet, so the tool doesn't yet improve from prior corrections.
- **No eval harness yet.** There is no hand-scored accuracy benchmark in this repo
  yet — that's the next planned slice, specifically so that changes to chunking,
  retrieval, or prompting can be measured as regressions or improvements instead of
  assumed.

## Tests

```
pytest tests/
```

`tests/test_pipeline_smoke.py` runs ingest, questionnaire parsing, and retrieval
against the real fixtures and asserts the pipeline reaches (and correctly stops at)
the Claude API boundary — it requires no API key and no network access beyond the
one-time embedding model download.

`tests/test_answerer_contract.py` asserts both `Answerer` implementations
(`AnthropicAnswerer`, `StubAnswerer`) return the same result shape, and that
`AnthropicAnswerer` raises rather than degrading when no API key is set.

## Fixtures

Everything under `fixtures/` is synthetic test data, invented for this repository —
not any real organization's policies or questionnaire. See the notice at the top of
each fixture file.
