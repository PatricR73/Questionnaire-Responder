**What it does:** turns a vendor security questionnaire spreadsheet into a first-pass
draft, citing your own security docs for every answer, and never inventing a control
you don't have.
**Who it's for:** B2B companies that get handed CAIQ/VSAQ-style questionnaires by
prospective enterprise customers and need a fast, honest first draft before a human
reviews it.
**What it costs you today:** a few cents of Claude API spend per question row, a CLI
and (optionally) a local Streamlit review screen — no hosting, no account, no
subscription. Measured 65% of a 20-question CAIQ set usable as-is; every other row is
either flagged for review or an honest "not found," never a confident wrong answer.
See Results below for the real numbers.

![Demo](docs/demo.gif)

# Vendor Security Questionnaire Responder

![CI](https://github.com/PatricR73/vendor-security-questionnaire-rag/actions/workflows/ci.yml/badge.svg)

Companies that sell to other businesses regularly get handed a long spreadsheet of
security questions by a prospective customer — "do you encrypt data at rest," "how
often do you review who has access to what," "can you delete our data on request" —
and someone has to answer every row correctly before the deal can close. This tool
reads a company's own security documentation (policies, procedures, plans) and drafts
first-pass answers to that spreadsheet automatically, citing exactly where each answer
came from. A person still reviews every answer before it goes out. The tool's job is
to make the first draft fast and honest, not to remove the human from the loop.

This is a CLI pipeline with an optional read-only review screen (`streamlit run
src/review_ui.py` — see Usage). The design rationale — why the tool never fabricates,
what is deliberately not built yet, and the v2 priority order — lives in
[`docs/DESIGN.md`](docs/DESIGN.md).

```mermaid
flowchart LR
    A[Evidence docs] --> B[Parse]
    B --> C[Chunk on sentence boundaries]
    C --> D[Embed locally]
    D --> E[(SQLite + Chroma)]
    Q[Questionnaire xlsx] --> F[Detect columns]
    F --> G[Question]
    G --> H[Hybrid retrieval: BM25 + vector, RRF fusion]
    E --> H
    H --> I[Generate draft — Anthropic API]
    I --> J[Cross-check: citation grounding + distance]
    J --> K[Write back into workbook]
    K --> L[Human review UI]
```

### Results at a glance

Measured against 20 real security-questionnaire questions with known-correct answers
(full breakdown and methodology in [`EVAL.md`](EVAL.md)):

| Outcome | Count |
|---|---|
| ✅ Usable (correct, ready to send or flagged for a quick check) | 13 / 20 (65%) |
| ⚠️ Confidently wrong / fabricated | **0 / 20** |
| 🚫 Scored "wrong" because it honestly abstained | 7 / 20 |

That 7/20 is scored as "wrong" against a strict correct/needs-editing/wrong rubric
even though every one of those rows is a safe, honest gap rather than a fabrication —
see [`EVAL.md`](EVAL.md) for the specific, verified cause behind each one.

## The core design constraint: this tool does not fabricate answers

The full reasoning, the two independent enforcement layers, and the review
requirements live in [`docs/DESIGN.md`](docs/DESIGN.md). The one-line version: the
tool will never assert a security control the organization has not documented, and
every output requires human review before it goes to a customer.

## Eval results

Measured against 20 real CAIQ v4.0.2 questions with known-correct answers, hand-scored
against a strict usable/needs-editing/wrong rubric: **65% usable, 0% needs-editing, 35%
wrong** — and every one of those "wrong" rows is an honest abstention or a named,
verified retrieval gap, never a confident fabrication (that failure mode measured at
0% both before and after tuning). Two tuning passes were run against this baseline
following a diagnose-from-data-first protocol; one was adopted (RRF fusion reweighting,
12/0/8 → 13/0/7), the other (raising the confidence threshold) was rejected because the
eval data shows no threshold value rescues the remaining failures without also breaking
a question that must stay abstained.

Reproduce these numbers yourself with `python fixtures/eval/run_eval.py`. Full
methodology, the per-question failure taxonomy, the threshold-tuning data, three real
bugs the harness caught before it ever scored anything, and two diagnoses that were
overturned by instrumenting instead of inferring: see **[`EVAL.md`](EVAL.md)**.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock   # pinned environment; requirements.txt is the loose declaration
pip install -e .          # optional: installs the `qresp` console command
```

The first `ingest` or `answer` run downloads a local sentence-transformers embedding
model (a few hundred MB). This can take a few minutes and looks like a hang — it isn't.

## Usage

Every command below can be run either as `python -m src.pipeline ...` (from the repo
root) or as `qresp ...` (after `pip install -e .`, from anywhere) — they are the
same CLI.

### 1. Ingest your evidence base

```
python -m src.pipeline ingest --evidence-dir path/to/your/evidence/
# or: qresp ingest --evidence-dir path/to/your/evidence/
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
# or: qresp answer --questionnaire path/to/questionnaire.xlsx --output out/filled.xlsx --limit 5
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
# or: qresp answer --questionnaire fixtures/questionnaire_sample.xlsx --output out/filled_stub.xlsx --limit 0 --provider stub
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

### 3. Review a completed run

```
streamlit run src/review_ui.py
```

Run this from anywhere — it works from a fresh clone with no extra setup (`pip
install -r requirements.txt` already covers it). This is a read-and-review surface
over the SQLite tables an `answer` run already wrote; it makes no Anthropic API calls
and doesn't re-run retrieval or generation. Pick a run in the sidebar, and for each
question you get the drafted answer side by side with the verbatim cited evidence
(source file + heading path), a confidence badge, and Approve / Edit / Reject buttons.
Approve/Reject record the decision; Edit saves your corrected text separately from the
model's original answer, so the eval-harness baselines in [`EVAL.md`](EVAL.md) stay
reproducible against what the model actually produced.

## What leaves your machine

This tool's stated buyer is a B2B security team, and the first question that buyer
asks is "where do my internal policies go." The answer, plainly:

**Runs entirely locally:** document parsing, chunking, embedding, retrieval (BM25 +
local vector index), the confidence cross-check, the workbook write-back, and the
review UI. None of those touch the network; the embedding model is downloaded once
and then runs on your machine.

**Sent to the Anthropic API — only during `answer`:** for each question row, the
system prompt, the question text, and the retrieved evidence passages (chunks of
your internal policies). Nothing else: no full documents, no workbook contents
beyond the question and the passages retrieval selected. `ingest`, `--dry-run`,
and the review UI make no API calls at all.

**Written to disk:** `out/store.db` (SQLite) holds your chunked policy text and
every drafted answer; `out/chroma/` holds the vector index of your policy text;
the sidecar `.jsonl` and `.log.jsonl` next to the output workbook hold drafted
answers and per-row detail. All of it is plaintext. The API key never touches disk
beyond an optional local `.env` file. [`.gitignore`](.gitignore) protects
`out/`, `.env`, and `.venv/` — nothing containing your policies or answers is
committed.

**Retention:** what Anthropic does with the prompts you send is governed by
Anthropic's current data-retention and privacy terms, which change over time —
read them directly rather than trusting a paraphrase here:
<https://docs.anthropic.com/en/docs/legal/data-usage>. Design for the assumption
that the question + retrieved passages leave your perimeter during `answer`.

**Review UI bind address:** `streamlit run src/review_ui.py` serves a database of
internal security policies — bind it to localhost explicitly:

```
streamlit run src/review_ui.py --server.address=127.0.0.1
```

## Design details

What is deliberately not built yet and why, plus the v2 priority order (what the eval
baseline actually showed blocking the score): see
[`docs/DESIGN.md`](docs/DESIGN.md).

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

`tests/test_whitespace_normalization.py` and `tests/test_malformed_response.py` are
regression tests for two of the three bugs the eval harness caught (see
[`EVAL.md`](EVAL.md)) — both built from the actual failing data the bug produced, not
a hypothetical case.

`tests/test_review_ui_entrypoint.py` executes `src/review_ui.py` under Streamlit's
actual `sys.path` setup (not just a curl against the running server, which never
triggers real script execution and would not have caught this) — regression test for
`streamlit run src/review_ui.py` failing with `ModuleNotFoundError` on a fresh clone.

## Fixtures

Everything under `fixtures/` is synthetic test data, invented for this repository —
not any real organization's policies or questionnaire. See the notice at the top of
each fixture file.

`fixtures/eval/` holds the eval harness: `questions.json` (the 20 labeled questions
behind the results above), `LABELING_GUIDE.md` (labeling methodology and the
anti-mirror sourcing rules), `TUNING_LOG.md` (every tuning pass, including the ones
that found no safe change to make), `questionnaire_eval.xlsx` (the same 20 questions
as an actual questionnaire workbook, generated from `questions.json` by
`make_eval_xlsx.py` — lets the eval set run through the real CLI, and doubles as a
20-row demo questionnaire instead of the 6-row sample), and `run_eval.py` (the
reproducible command behind the results in [`EVAL.md`](EVAL.md)). The eval questions
are quoted verbatim from the real CSA CAIQ v4.0.2 instrument under fair use with
attribution — see the licensing section in `LABELING_GUIDE.md` for what is and isn't
safe to commit from that source.
