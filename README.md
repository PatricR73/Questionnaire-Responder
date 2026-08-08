# Vendor Security Questionnaire Responder

Companies that sell to other businesses regularly get handed a long spreadsheet of
security questions by a prospective customer — "do you encrypt data at rest," "how
often do you review who has access to what," "can you delete our data on request" —
and someone has to answer every row correctly before the deal can close. This tool
reads a company's own security documentation (policies, procedures, plans) and drafts
first-pass answers to that spreadsheet automatically, citing exactly where each answer
came from. A person still reviews every answer before it goes out. The tool's job is
to make the first draft fast and honest, not to remove the human from the loop.

This is a CLI pipeline. There is no web UI, no review interface, and no write-back of
human-approved answers into the evidence base yet — see "What's deliberately not built
yet" below for why, and in what order that's likely to change.

## The core design constraint: this tool does not fabricate answers

A wrong answer on a security questionnaire isn't a typo — it's a written
representation to a prospective customer about what the company actually does. If the
tool asserts a control that doesn't exist ("yes, we run quarterly penetration tests")
because that's what a security questionnaire *usually* says, that's not a bug the
customer will shrug off; it's a false statement in a document that may end up
referenced in a contract. So the tool is built around one non-negotiable rule: **it
will never assert a security control the organization has not documented.** If the
evidence base doesn't support an answer, the tool does not guess, does not extrapolate
from "typical" industry practice, and does not fill the gap with a plausible-sounding
claim. That row is left with the literal marker `NOT FOUND IN PROVIDED DOCUMENTS`
instead. An honest gap is a cheap problem — someone writes the missing policy or
answers the row by hand. A confident fabrication is an expensive one, discovered only
when it's already been relied on.

This is enforced twice, independently, on purpose — one mechanism trusting itself is
exactly how this kind of guarantee quietly erodes:
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

## Eval results

Every claim in this section is measured against `fixtures/eval/questions.json` — 20
questions quoted verbatim from the real CSA CAIQ v4.0.2 instrument (not invented, not
paraphrased), hand-labeled against a synthetic evidence corpus *before* the pipeline
was ever run against them, by a process specifically designed to avoid the tool
grading its own homework: the evidence corpus, the questions, and the expected labels
were sourced and reviewed independently, in that order, with each stage committed
separately. Full methodology in `fixtures/eval/LABELING_GUIDE.md`; every tuning
decision and its data in `fixtures/eval/TUNING_LOG.md`.

### The number: 60% → 65% usable, honestly

Every one of the 20 answers was read — not pattern-matched against the label — and
scored `usable` (correct and ready to send, possibly flagged for review as designed),
`needs-editing` (right idea, wrong in a way a human would have to fix), or `wrong`
(silently missed real evidence, or answered something it shouldn't have).

| | Baseline | After tuning |
|---|---|---|
| usable | 12 (60%) | 13 (65%) |
| needs-editing | 0 | 0 |
| wrong | 8 (40%) | 7 (35%) |

**The 0 in needs-editing is not a rounding error, and it's worth reading correctly.**
This system's failure mode is binary: it either produces a complete, accurate,
appropriately-hedged answer, or it silently says nothing. It doesn't produce answers
that are subtly wrong, half-right, or confidently overreaching — every wrong case
below is a *missing* answer, never a *bad* one. That's a direct, measured consequence
of the no-fabrication design above, and it's a meaningfully safer failure profile than
a system that fails by being wrong in ways a reviewer might not catch.

**65% is not a high number, and this document is not going to dress it up as one.**
The value of this baseline isn't the percentage — it's that every one of the 7
remaining wrong answers has a specific, named, verified cause, not a shrug:

| Question | Cause | Fixable how |
|---|---|---|
| `IAM-13.1`, `BCR-03.1`, `SEF-06.1`, `UEM-13.1`, `SEF-02.1`, `BCR-08.2` | Confidence threshold discards a correct, well-hedged answer because the best retrieved chunk's distance exceeds `WEAK_MATCH_DISTANCE` | **Nothing available today** — see the threshold finding below. Needs a bigger/more diverse corpus or a different confidence signal, not a parameter tweak. |
| `STA-09.1` | Compound question (9 clauses); the question's embedding dilutes across every topic it touches and matches none of them strongly, so the model abstains on the whole question rather than answering the covered parts | Real compound-question splitting (currently a pass-through stub) |

Two tuning passes were run against this baseline, each following the same protocol:
diagnose from data before touching anything, change one thing, run the full 20 twice
to check for run-to-run variance, and treat "a `NOT_FOUND` question starts answering"
as a disqualifying regression regardless of what it does to the total.

- **Confidence threshold (`WEAK_MATCH_DISTANCE`)** — no change made. See below.
- **RRF fusion weighting** (`src/retrieval/hybrid_search.py`) — equal-weighted BM25 and
  vector scores let BM25 term mismatch bury two chunks with excellent vector distance
  outside the top-5 the model ever sees. Weighting vector rank 2x fixed `IAM-08.1`
  outright and confirmed `BCR-08.2` was never a retrieval problem in the first place
  (its evidence is now retrieved at rank 2; the model still doesn't use it — see the
  threshold table above). Zero variance across two full re-runs, all 6 `NOT_FOUND`
  questions held both times. This is the one adopted change; 12/0/8 → 13/0/7.

### The threshold finding: distance alone doesn't separate "weak but real" from "absent"

The obvious next move after the RRF fix was to raise `WEAK_MATCH_DISTANCE` (currently
`0.3`) to rescue the remaining six threshold-blocked cases. The data says don't:

| dist | question | status |
|---|---|---|
| 0.314 | `BCR-08.2` (wrong) | self-abstain, threshold can't help |
| **0.320** | **`LOG-12.1` (must stay NOT_FOUND)** | — |
| **0.321** | **`BCR-08.3` (must stay NOT_FOUND)** | **itself threshold-sensitive** |
| 0.323 | `UEM-13.1` (wrong) | needs threshold ≥ 0.323 to rescue |
| 0.340 | `SEF-06.1` (wrong) | needs threshold ≥ 0.340 to rescue |
| 0.359 | `IAM-13.1` (wrong) | needs threshold ≥ 0.359 to rescue |
| 0.384 | `SEF-02.1` (wrong) | needs threshold ≥ 0.384 to rescue |
| 0.412 | `BCR-03.1` (wrong) | needs threshold ≥ 0.412 to rescue |

`BCR-08.3` — one of the six questions that must always abstain — sits at 0.321 and is
itself only correctly abstaining *because* 0.321 exceeds 0.3. Every wrong case above
needs a threshold higher than 0.321 to be rescued. There is no value that fixes any of
them without also flipping `BCR-08.3` into confidently answering a question its
evidence doesn't support — which the eval treats as automatically disqualifying,
regardless of the total score. `BCR-08.3` was deliberately built to sit at the
threshold edge, specifically to test whether the threshold does anything; it's doing
its job by proving there's no clean separation point in this corpus at this size, not
by being miscalibrated. Full data in `fixtures/eval/TUNING_LOG.md`.

### What the eval harness caught before it ever scored anything

Three real bugs, found by building the eval fixtures and running the pipeline for the
first time against real API calls — none of them related to labeling or scoring at all:

1. **Ingestion boilerplate pollution.** Every evidence file's confidentiality
   disclaimer (a real-world stand-in for a header/footer/title page any actual
   evidence pack would have) was being embedded as its own searchable chunk. It was
   winning as a false-positive distractor in roughly half of a sample question set —
   including beating out the real evidence for one of the eval fixtures. Fixed by
   dropping pre-first-heading front matter at chunk time (`src/ingest/chunk.py`).
2. **Grounding-check false negatives on hard-wrapped source text.** Source markdown
   files are hard-wrapped at ~85 characters; chunk text carried a literal newline at
   each wrap point. A model quoting a citation naturally used normal spacing instead,
   so the byte-exact grounding check in `confidence.py` failed 3 of the first 5 real
   answers — all of which were actually correct and well-cited. Fixed by normalizing
   whitespace at chunk-storage time, not just at the comparison site, so the same
   artifact can't resurface anywhere else chunk text gets displayed or matched.
3. **Malformed structured output under longer, more hedged reasoning.** 3 of the first
   20 real Sonnet calls returned a response with a required field entirely missing,
   replaced by what looked like leaked tool-call formatting bleeding into the answer
   text. `tool_choice`-forced tool use — the mechanism in place at the time — turned
   out to be a hint, not an enforced constraint. Fixed by switching to the Anthropic
   API's structured-output mode (constrained decoding against the schema, not a
   request for schema-shaped output), with defensive validation and one corrective
   retry as the backstop, and a regression test built from the actual malformed
   payload the bug produced — not a hypothetical one.

None of these were prompted by an accuracy score dropping. All three were found because
the harness demanded looking directly at real output instead of trusting that the
pipeline was doing what it was designed to do.

### Two diagnoses the harness later overturned by verifying instead of inferring

- **`UEM-13.1`** was first logged as "retrieves cleanly at rank 1" based on something
  being retrieved from the right *document*. Checking the actual retrieved *content*
  showed the top result was a different, wrong section entirely — the real evidence
  was buried at rank 4. The label stayed the same (the evidence really does support an
  `AFFIRMS`), but the retrieval-quality claim was wrong until it was checked directly.
- **`BCR-03.1`, `SEF-06.1`, `BCR-08.2`** were reported as "the model saw the evidence
  and chose to self-abstain anyway" — a generation-prompt problem, planned as the next
  tuning target. Directly instrumenting the actual `self_confidence` and `supported`
  values (rather than inferring them from the final `NOT_FOUND` status, which can't
  distinguish self-abstention from a threshold-downgraded answer) showed the model was
  answering correctly every time; the confidence threshold was silently discarding a
  good answer afterward. There was no generation-prompt bug to fix — the "over-caution"
  diagnosis was an inference from an aggregate result, not a checked fact, and it was
  wrong. This is the same lesson as `UEM-13.1`, one level further downstream: an
  aggregate status (`NOT_FOUND`) collapses two genuinely different causes into one
  observable outcome, and only reading the underlying values tells them apart.

Two label corrections came out of the same discipline, applied to the eval fixtures
themselves rather than the code: `IAM-02.1` was dropped from the `AFFIRMS` bucket
before the baseline even ran (its question text matches a policy-governance template
this corpus has no content for — a mislabel caught by comparing question phrasing
against corpus content, not a system disagreement). `IVS-03.2` was moved from
`AFFIRMS` to `PARTIAL` after hand-scoring the baseline, when re-reading its evidence
against the question showed the evidence only covers production-internal traffic, not
"environments" broadly — the system's own hedged answer was more correct than the
label it was being measured against. Both corrections were made because the evidence
said the label was wrong, never because the system disagreed with it — see
`fixtures/eval/LABELING_GUIDE.md` and the relevant commits for the reasoning.

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

## What's deliberately not built yet, and why

Each of these was scoped out on purpose, not forgotten — building the core pipeline
and a real eval harness first meant every later addition gets measured against a known
baseline instead of just assumed to help:

- **Compound question splitting is a stub.** Multi-part questions ("Do you do X, and
  how often is Y?") are answered as a single question rather than split into parts.
  `STA-09.1` in the eval set exists specifically to measure this gap (it's the one
  remaining wrong case not caused by the confidence threshold) — real splitting was
  deferred until there was a number showing how much it actually costs, rather than
  built speculatively.
- **No review UI.** Review means opening the output workbook directly. A UI is only
  worth building once there's a working, measured pipeline underneath it to review the
  output of — building it first would mean reviewing a system nobody had evidence
  about yet.
- **No feedback loop.** Human-approved/edited answers are not written back into the
  evidence base yet, so the tool doesn't improve from prior corrections. Same
  reasoning: writing corrections back into evidence the retrieval/confidence system
  hasn't been tuned against yet would make it harder, not easier, to tell whether a
  later change was an improvement.
- **PDF parsing is unvalidated.** `src/ingest/parse_docs.py` can parse PDFs, but
  heading detection there is a font-size/layout heuristic with no PDF fixture tested
  against it yet, and it assumes a born-digital PDF (real text, not a scanned image).
  Don't trust it on a real PDF without checking the resulting chunks first.

## v2 priority order

In order, based on what the eval baseline actually showed blocking the score — not a
wishlist:

1. **A confidence signal that isn't a single flat distance threshold.** This is what
   6 of the 7 remaining wrong answers trace back to, and the eval data shows
   conclusively that no value of `WEAK_MATCH_DISTANCE` fixes it for this corpus (see
   the threshold finding above) — the "weak but real" and "genuinely absent" distance
   clusters overlap. Likely needs a larger, more varied corpus to find a real
   separation point, a different signal entirely (e.g. distance of the specific cited
   chunk rather than the best distance across everything retrieved), or both.
2. **Real compound-question splitting.** The other remaining wrong case, and the only
   one not blocked by the threshold problem above — independently fixable now.
3. **Expand the eval corpus.** Both the threshold redesign above and any future
   retrieval/prompt change need a bigger, more diverse fixture set to measure against
   than 20 questions over 3 documents; this baseline is deliberately small and honest
   about that, not a finished instrument.
4. **Review UI**, once the above gives reviewers something worth trusting a first
   draft from more often than 65% of the time.
5. **Feedback loop** (write approved/edited answers back into the evidence base), after
   the review UI exists to produce that signal in the first place.
6. **PDF fixture validation**, and CSV/other questionnaire formats — lower priority
   because no real user of this tool has hit either gap yet; speculative until then.

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
regression tests for two of the three bugs the eval harness caught (see "Eval
results" above) — both built from the actual failing data the bug produced, not a
hypothetical case.

## Fixtures

Everything under `fixtures/` is synthetic test data, invented for this repository —
not any real organization's policies or questionnaire. See the notice at the top of
each fixture file.

`fixtures/eval/` holds the eval harness: `questions.json` (the 20 labeled questions
behind the results above), `LABELING_GUIDE.md` (labeling methodology and the
anti-mirror sourcing rules), and `TUNING_LOG.md` (every tuning pass, including the
ones that found no safe change to make). The eval questions are quoted verbatim from
the real CSA CAIQ v4.0.2 instrument under fair use with attribution — see the
licensing section in `LABELING_GUIDE.md` for what is and isn't safe to commit from
that source.
