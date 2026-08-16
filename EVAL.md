# Eval methodology and results

Full detail behind the numbers summarized in the README's "Results at a glance."

Every claim in this document is measured against `fixtures/eval/questions.json` — 20
questions quoted verbatim from the real CSA CAIQ v4.0.2 instrument (not invented, not
paraphrased), hand-labeled against a synthetic evidence corpus *before* the pipeline
was ever run against them, by a process specifically designed to avoid the tool
grading its own homework: the evidence corpus, the questions, and the expected labels
were sourced and reviewed independently, in that order, with each stage committed
separately. Full methodology in `fixtures/eval/LABELING_GUIDE.md`; every tuning
decision and its data in `fixtures/eval/TUNING_LOG.md`.

Reproduce these numbers yourself with:

```
pip install -r requirements.lock   # the pinned environment these numbers were measured in
python fixtures/eval/run_eval.py
```

**Locked environment.** `requirements.txt` declares lower bounds only — chromadb
and sentence-transformers can change distance semantics and default model
revisions out from under `WEAK_MATCH_DISTANCE = 0.3`. The numbers in this
document and in `fixtures/eval/TUNING_LOG.md` were produced in the environment
pinned by [`requirements.lock`](requirements.lock) (Python 3.14, chromadb 1.5.9,
sentence-transformers 5.7.0) with the embedding model pinned to
`BAAI/bge-small-en-v1.5@5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
(`src/store/vectorstore.py`). That pin is the reproducibility path; `requirements.txt`
stays the loose declaration.

This is the actual documented command, not a summary of one — earlier runs this
session used one-off scripts that called the model directly, bypassing the CLI
entirely, which meant the thing being measured wasn't quite the thing anyone would
actually run. `run_eval.py` shells out to the real `python -m src.pipeline answer`
against `fixtures/eval/questionnaire_eval.xlsx` (the same 20 questions as
`questions.json`, generated from it by `fixtures/eval/make_eval_xlsx.py` so the two
can't drift apart) and scores the result — the eval path and the CLI path are the
same code. It prints the structural match (status/polarity vs. expected label) and
every full answer for hand-scoring, and flags a `NOT_FOUND` question coming back
`ANSWERED` as a regression on its own line, regardless of the total — it does not
compute usable/needs-editing/wrong itself, since that's a deliberately hand-scored
judgment call (see the methodology above), not something to automate.

## The number: 60% → 65% usable, honestly — then the adversarial subset changed the picture

The 60% → 65% numbers below are the historical hand-scored baseline on the original
20-question set. The corpus has since grown to 24 questions with an adversarial
subset (P33), and the current measured baseline is the structural match:
**14/24 across three deterministic runs** — with two of the four adversarial
questions answered as documented facts. That is the first measured breach of the
no-fabrication guarantee, and it changes what the honest summary looks like:

| | Historical (20 questions, hand-scored) | Current (24 questions, structural) |
|---|---|---|
| usable / structural match | 13 (65%) | 14 / 24 (58%) |
| needs-editing | 0 | n/a (pending blind re-score) |
| wrong | 7 (35%) | 8 / 24 — of which **2 are fabrications** (ADV-02, ADV-04) |

The two fabrications are not a regression in the model's behavior; they are the
adversarial subset doing its job. The guarantee was previously only *observed* to
hold (no question actively tried to break it); ADV-02 (off-site backup storage) and
ADV-04 (an IGA platform for access reviews) are plausibly implied by the evidence
but not documented, and the model answered both. Any claim of "0% fabricated" now
comes with the qualifier "on the questions that were actively trying to catch one —
two of four slipped through."

**The 0 in needs-editing was not a rounding error on the historical set, and it is
still worth reading correctly as far as it went.** This system's failure mode was
binary: it either produced a complete, accurate, appropriately-hedged answer, or it
silently said nothing. The adversarial subset shows the binary can fail in the other
direction too — confidently asserting an implied control as documented — which is
precisely why the subset exists. The usable/needs-editing/wrong hand-scores for the
24-question set await a blind re-score per the scoring protocol in
`fixtures/eval/LABELING_GUIDE.md`.

**65% was not a high number then, and 58% is not one now — and this document is not
going to dress either up.**
The value of a baseline isn't the percentage — it's that every wrong answer has a
specific, named, verified cause, not a shrug:

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

## The threshold finding: distance alone doesn't separate "weak but real" from "absent"

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

## What the eval harness caught before it ever scored anything

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

## Two diagnoses the harness later overturned by verifying instead of inferring

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
