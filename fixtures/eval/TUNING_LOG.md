# Tuning log

Records each deliberate tuning pass against the 20-question eval fixture: what was
changed, the before/after usable/needs-editing/wrong counts, and — most importantly —
what was tried and rejected, with why. A tuning log that only records adopted changes
hides exactly the reasoning a future session (or a client asking "why does the
threshold sit where it does") would need. Baseline (pre-tuning): **12 usable / 0
needs-editing / 8 wrong**, hand-scored against `fixtures/eval/questions.json`, model
`claude-sonnet-5`.

**Provider note (2026-08-18):** passes 1–8 below were measured on **Claude (`claude-sonnet-5`)**,
the generation provider before this date. They are Claude measurements and are **not comparable** to
anything measured after this commit — the baseline provider moved to DeepSeek (`deepseek-v4-flash`)
on that date (see EVAL.md's current-baseline section). Any pass added after this point measures a
different model and must say so explicitly.

## Pass 1 — confidence threshold (`WEAK_MATCH_DISTANCE` in `src/answer/confidence.py`)

**Result: no change made. The data rules out every candidate value.**

Current value: `0.3`. Retrieval distance for all 20 questions (best chunk distance,
`top_k=5`), with each question's path through `cross_check_confidence` — `self-abstain`
means the model itself returned `supported=False` and never reaches the distance
check at all; `threshold-sensitive` means the model answered (`self_confidence` "low"
or "high") and the distance check is what decides whether that survives:

| dist | question | expected | path |
|---|---|---|---|
| 0.232 | `CEK-08.1` | ANSWERED_DENIES | threshold-sensitive |
| 0.249 | `BCR-08.1` | ANSWERED_AFFIRMS | threshold-sensitive |
| 0.249 | `IVS-03.2` | ANSWERED_PARTIAL | threshold-sensitive |
| 0.261 | `IAM-14.1` | AMBIGUOUS_EVIDENCE | threshold-sensitive |
| 0.280 | `CEK-01.1` | ANSWERED_PARTIAL | threshold-sensitive |
| 0.293 | `IAM-15.1` | AMBIGUOUS_EVIDENCE | threshold-sensitive |
| 0.314 | `BCR-08.2` | ANSWERED_PARTIAL (wrong) | self-abstain |
| **0.320** | **`LOG-12.1`** | **NOT_FOUND** | self-abstain |
| **0.321** | **`BCR-08.3`** | **NOT_FOUND** | **threshold-sensitive** |
| 0.323 | `UEM-13.1` | ANSWERED_AFFIRMS (wrong) | threshold-sensitive |
| 0.340 | `SEF-06.1` | ANSWERED_AFFIRMS (wrong) | threshold-sensitive |
| **0.341** | **`DSP-14.1`** | **NOT_FOUND** | self-abstain |
| **0.353** | **`HRS-01.1`** | **NOT_FOUND** | self-abstain |
| 0.355 | `IAM-08.1` | ANSWERED_AFFIRMS (wrong) | self-abstain |
| 0.359 | `IAM-13.1` | ANSWERED_AFFIRMS (wrong) | threshold-sensitive |
| 0.375 | `STA-09.1` | ANSWERED_PARTIAL (wrong) | self-abstain |
| 0.384 | `SEF-02.1` | ANSWERED_PARTIAL (wrong) | threshold-sensitive |
| **0.384** | **`SEF-07.1`** | **NOT_FOUND** | self-abstain |
| **0.390** | **`A&A-02.1`** | **NOT_FOUND** | self-abstain |
| 0.412 | `BCR-03.1` | ANSWERED_AFFIRMS (wrong) | threshold-sensitive |

**Why no value works:** `BCR-08.3` is a required-`NOT_FOUND` question and is itself
threshold-sensitive at 0.321 — it currently abstains only because 0.321 exceeds the
0.3 threshold. Every wrong case a higher threshold could rescue sits above that:
`UEM-13.1` 0.323, `SEF-06.1` 0.340, `IAM-13.1` 0.359, `SEF-02.1` 0.384, `BCR-03.1`
0.412. Any threshold high enough to fix even one of them is also high enough to flip
`BCR-08.3` from abstaining to answering, which is a hard-disqualifying regression
regardless of what it does to the total (see the eval methodology: never let a
NOT_FOUND question start answering). Two more wrong cases, `BCR-08.2` and `IAM-08.1`,
are self-abstain — the threshold was never going to touch them at all, at any value.

`BCR-08.3` was deliberately built (see `LABELING_GUIDE.md`) to sit at the threshold
edge, specifically to test whether the threshold does anything. It's doing its job:
it's telling us the edge is genuinely contested ground in this corpus, not that 0.3
happens to be wrong. Lowering the threshold isn't useful either — it only downgrades
more cases to `none`, the opposite of what fixing the 8 wrong cases needs.

**Conclusion:** confidence-threshold tuning is exhausted for this corpus at this size.
Revisit only if the corpus grows enough to separate the "weak but real" and
"genuinely absent" distance clusters that currently overlap.

## Pass 2 — RRF fusion weighting (`VECTOR_WEIGHT` in `src/retrieval/hybrid_search.py`)

**Result: 12/0/8 → 13/0/7. `VECTOR_WEIGHT = 2.0` adopted.**

Before touching anything, checked which of the 7 threshold-uninvolved wrong cases were
actually retrieval-visibility problems rather than something the confidence path
already covered. Only 2 of 7 were: `IAM-08.1` and `BCR-08.2` both had their target
chunk at **vector rank 1** (dist 0.287 and 0.310, both comfortably under
`WEAK_MATCH_DISTANCE`) but **BM25 rank 13 of 14** — near dead last — and equal-weighted
RRF split the difference, landing both at combined rank 6-7, just outside the
`top_k=5` the model receives. The other 5 wrong cases (`IAM-13.1`, `BCR-03.1`,
`SEF-06.1`, `UEM-13.1`, `SEF-02.1`) already had their target chunk inside the top-5 —
reranking cannot help them; their bottleneck is the confidence threshold (see Pass 1)
or the model declining to use evidence it already has.

Simulated `VECTOR_WEIGHT` at 1.0 (baseline), 1.5, 2.0, 3.0 against the full 20-question
set before applying anything. 1.5 was the minimum that pulled both targets into the
top-5, but left `BCR-08.2` at rank 5 — the literal edge, fragile to any future
embedding drift. 2.0 gave both solid margin (`IAM-08.1` rank 3, `BCR-08.2` rank 2) with
no further gain at 3.0 — the natural plateau. All four weights only ever *reordered*
existing `NOT_FOUND` distractor sets, never introduced new content.

Applied `VECTOR_WEIGHT = 2.0`, then ran the full 20 twice (per the run-to-run variance
found during the threshold pass). Identical status on every question, both passes —
no drift.

- `IAM-08.1`: `NOT_FOUND` → `ANSWERED` (low/partial). Correctly hedged: confirms review
  cadence exists but declines to confirm the reviews serve least-privilege/
  separation-of-duties purposes or were risk-based, since the evidence doesn't say
  either. Worth a look as a possible future relabel to `PARTIAL` (similar reasoning to
  the `IVS-03.2` correction) — not changed here, flagged for a deliberate pass.
- `BCR-08.2`: **unchanged**, still `NOT_FOUND`, both passes. Its target chunk now sits
  at rank 2 — well inside the top-5 — but the model still self-abstains rather than
  answering about backup availability. This confirms `BCR-08.2` was never a retrieval
  problem; it's the same generation-side over-caution pattern as `BCR-03.1`/`SEF-06.1`.
  Reranking has done everything it can for this one.
- All 6 `NOT_FOUND` questions (`LOG-12.1`, `A&A-02.1`, `HRS-01.1`, `DSP-14.1`,
  `BCR-08.3`, `SEF-07.1`) stayed `NOT_FOUND` on both passes — no regression.

**Conclusion:** RRF-weighting tuning has reached its ceiling for this corpus too — the
remaining wrong cases split between the exhausted confidence threshold (Pass 1) and a
new, distinct failure mode: the model declining to answer from evidence it already has
in context (`BCR-03.1`, `SEF-06.1`, `BCR-08.2`). That's a generation-prompt question,
not a retrieval question — a different tuning pass than either of the first two.
## Pass 3 — bge query instruction prefix (`src/store/vectorstore.py`)

**Result: code landed; generation-side before/after BLOCKED on API credits.**

BAAI's bge-*-en-v1.5 models are trained with an asymmetric setup: the query side must
carry "Represent this sentence for searching relevant passages: " while passages stay
unprefixed. Applied on the query path only.

- **Baseline (before, 24-question set, 3 repeats):** structural match **14/24**,
  deterministic across runs. **The adversarial subset (P33) caught its first
  fabrications: `ADV-02` (off-site backup storage) and `ADV-04` (IGA-platform
  access reviews) came back ANSWERED** — both plausibly implied by the evidence but
  not documented, exactly the trap they were built to spring. The no-fabrication
  guarantee, previously only observed to hold, failed its first active stress test.
  No polarity inversions.
- **Retrieval-side (local, no API):** 5/24 → 6/24 questions retrieve a best chunk
  at cosine distance ≤ 0.3 with the prefix; average best distance 0.3515 → 0.3547.
  Marginal on this 14-chunk corpus — the prefix's recall benefit shows at scale.
- **Generation-side after:** not measured — the API credit balance ran out during
  the re-run. (The P13 fatal-error handling aborted the run cleanly on the first
  row rather than burning all 24, which is the behavior it was built for.) The
  before/after structural comparison is pending a credit top-up and a re-run.

## Pass 4 — BM25 zero-score filter, three ways (`src/retrieval/hybrid_search.py`)

**Result: filter kept; the VECTOR_WEIGHT question deferred — the three-way
generation comparison is blocked on API credits.**

Measured locally (retrieval side, no API), the question count whose best chunk lands
at cosine distance ≤ 0.3 across the 24-question set:

| config | retrievable | avg best distance |
|---|---|---|
| (a) filter on, VECTOR_WEIGHT = 2.0 | 5/24 | 0.3515 |
| (b) filter on, VECTOR_WEIGHT = 1.0 | 5/24 | 0.3519 |
| (c) control (no filter), VECTOR_WEIGHT = 2.0 | 5/24 | 0.3515 |

The filter is behavior-neutral on this corpus because it is smaller than the
candidate pool (14 chunks < CANDIDATE_POOL = 20), so zero-score chunks never entered
the pool anyway — it will matter at scale, and it is correct to keep. The
task's decision rule — if (b) matches or beats (a), remove the weighting — needs the
generation-side structural scores, which require API credits; the earlier
VECTOR_WEIGHT = 2.0 tuning pass (Pass 2) may have been compensating for the
zero-score credit that this filter removes, but that conclusion needs the data.

## Pass 5 — cross-encoder reranker (`src/retrieval/reranker.py`, flag off by default)

**Result: flag landed; generation-side with/without BLOCKED on API credits.**

Retrieval side (local, no API): reranker off → 5/24 retrievable, avg best 0.3515,
avg top-1 0.3694; reranker on → 4/24 retrievable, avg best 0.3660, avg top-1 0.3991.
On this 14-chunk corpus the cross-encoder re-ordering slightly degrades the
distance proxy (it ranks by relevance, which can push the nearest-distance chunk out
of top-5). The rerank score is carried on RetrievedChunk and NOT wired into
cross_check_confidence, per the commit's scope; the confidence change stays a
separate decision with its own data. The with/without generation comparison is
pending credits.

## Pass 6 — chunk ceiling and sentence overlap (`src/ingest/chunk.py`)

**Result: code landed; generation-side before/after BLOCKED on API credits.**

Retrieval side (local, no API), old chunker vs new: both 5/24 retrievable, avg best
0.3515 — retrieval-equivalent on this corpus. The chunking change's value is not the
distance: it is the guarantee that no sentence is severed at a chunk boundary (the
verbatim citation-grounding check depends on that) and that no chunk exceeds the
ceiling. Those are enforced by tests, not by the eval; the generation-side
structural comparison is pending credits.

## Blocked-work note

The generation-side before/after comparisons for Passes 3-6 all require API credits,
which ran out mid-Pass-3. The retrieval-side measurements above are recorded so the
passes are not empty; each needs a re-run (`python fixtures/eval/run_eval.py
--repeats 3`) with a positive credit balance to complete. The completed baseline —
24 questions, 14/24 structural match, ADV-02/ADV-04 fabrications — is the reference
point everything else compares against.
## Pass 7 — entailment (support) check (`src/answer/entailment.py`), A1

**Result: layer implemented behind a flag (default OFF); live eval BLOCKED on API
credits; the motivating "fabrications" were re-examined and one was a mislabel.**

The adversarial run marked `ADV-02` and `ADV-04` as fabrications — the model
"quoted real sentences, passed grounding, and asserted a control those sentences
merely make plausible". Reading the REAL captured data (run A store) contradicts that
for both:

- **`ADV-02` is not a fabrication — the label was wrong.** The captured citation,
  "Backups are stored in a separate cloud region from the primary production
  environment.", is verbatim in `business_continuity_plan.docx` and DIRECTLY states
  the claimed control. The labeling-guide note claimed the storage location was "not
  documented" — the evidence says otherwise. `ADV-02` was relabeled
  `ANSWERED_AFFIRMS` on the evidence (same rule as the `IVS-03.2` correction). The
  entailment layer correctly does NOT flag a grounded claim.
- **`ADV-04`'s answer text is a correct abstention.** Its captured answer says the
  IGA claim "cannot be confirmed" and its factual claims are all grounded; the defect
  is that the structured output reported `supported=true`, so the pipeline recorded
  `ANSWERED`. That is a status-mapping/prompt-compliance issue, not an entailment
  failure. The entailment layer must not flag a grounded hedge.

The layer itself is still the right architecture for the gap the pack identified —
grounding checks citation fidelity, not whether the answer follows from the
citations — and it is implemented exactly as specified: a separate cheap Claude call
receiving ONLY the drafted answer and cited sentences (no question, no other chunks),
behind the `entailment_check` config flag (default OFF so the baseline stays
reproducible), using `entailment_model` (default `claude-sonnet-5`, set to the
cheapest capable tier on the account), with its tokens logged separately in the run
summary so the price of the guarantee is visible. The mechanism is proven by a
constructed over-assertion (an answer claiming an underground bunker, absent from its
citation) which is downgraded to `none`.

**Eval status:** the `--repeats 3` with/without comparison could not be run — the
API credit balance is still exhausted (every `answer` row calls the checker when the
flag is on, so both legs need credits). The false-positive cost (how many correct
answers the checker wrongly kills) is therefore unmeasured and the flag stays OFF.
Re-run `python fixtures/eval/run_eval.py --repeats 3` with the flag on and off once
credits are available; if the checker kills more good answers than it catches, that is
a real result and the flag stays off.

**Baseline note:** with `ADV-02` relabeled to `ANSWERED_AFFIRMS`, the published
14/24 baseline is now 15/24 (the ADV-02 row was counted as a NOT_FOUND regression
before; it is a match under the corrected label) — the corrected number replaces the
old one in README/EVAL.


## Pass 8 — answer library (C4): separate namespace, freshness-gated, flag OFF

**What changed:** the feedback loop is now built — but as the answer library, not as
"approved answers written back into the evidence base". A separate
`reviewed_answers` namespace (SQLite) stores human-approved/edited answers with
full provenance (original question, source run, row, action, timestamp, and the
content hashes of the source docs the answer was grounded in). At answer time, a
non-stale, semantically-equivalent prior answer is surfaced to the generator as a
labelled CANDIDATE (never as evidence: HybridSearcher reads only the chunks table,
and citation/entailment checks run against the original evidence only). Rows that
draw on the library are marked with a third fill colour + provenance comment in the
workbook, and the eval can measure the library's effect via the `answer_library`
config flag (default OFF so the published baseline stays reproducible).

**Why the separate namespace (the design constraint that makes it safe):** writing
approved answers into the retrieval pool would manufacture confident,
human-endorsed, WRONG answers — a Q1-approved answer citing policy v3 outranks
policy v4 in Q3, source traceability erodes, and reviewer errors become training
signal. The library can only be a candidate pool, gated by freshness: an entry
whose source documents have changed (content hashes recorded at ingest) is
excluded, and an entry with no verifiable snapshot is excluded too. A prior answer
is a strong prior, not a source of truth.

**Measured (stub, mechanics only — no API spend):** with the library seeded from
the curated demo store and `answer_library=true`, exact normalized matches return
similarity 1.0 and semantic near-equivalents return 0.75-0.93 in the same embedding
space as retrieval; stale entries (source hash changed) are excluded by the
freshness gate; approved answers are invisible to HybridSearcher (structural
isolation test); and a full stub run records `library_state`/provenance per row
with the workbook marker applied. The structural-match score is unchanged by the
flag (retrieval untouched; candidates only extend the prompt).

**Pending (needs API credits):** the real-provider delta — run
`python fixtures/eval/run_eval.py --repeats 3` with `answer_library=true`
seeded from a prior real run, and record the structural-match delta against the
15/24 baseline. Until that measurement exists, the flag stays OFF. If the delta is
negative, that is a real result; the library stays off and the candidate-presentation
is redesigned.

## Defect fix (2026-08-18) — max_tokens raised; recorded as a defect fix, not a tuning pass

**Not a tuning pass:** nothing about the model's answer quality was changed or tuned.
A fixed defect in the generation budget — the OpenAI-compatible transport was silently
ignoring the configured max_tokens and truncating long answers.

**Evidence (the two truncated rows, from the pre-fix DeepSeek `--repeats 3` run):**

| Run | Row | Question | Failure |
|---|---|---|---|
| 1 | 20 | `IAM-15.1` (AMBIGUOUS_EVIDENCE) | `AnswerTruncatedError: Response truncated (finish_reason=length) even at 2048 tokens` |
| 2 | 21 | `IAM-14.1` (AMBIGUOUS_EVIDENCE) | `AnswerTruncatedError: Response truncated (finish_reason=length) even at 2048 tokens` |

Both are rule-8 conflicting-evidence questions: the answer must present both claims
attributed to their sources plus several verbatim cited_sentences, which the model
legitimately produces at ~2.3–2.9k output tokens (run 3 of the same set completed them
at 2236–2901 tokens). The pre-fix budget was a Claude-era leftover — `MAX_TOKENS = 1024`
with a 2x truncation retry at 2048 — and both rows hit `finish_reason=length` even at
the retried limit. DeepSeek's API supports far more output than Claude was budgeted
for, so the cap itself was the defect, not the answers.

**What changed (all config-driven, no new hardcoded limit):**

1. `MAX_TOKENS` default raised `1024 → 4096` in `src/answer/generate.py` (the
   single source of truth `Config.max_tokens` defaults to; the 2x truncation retry
   follows it to 8192). Overridable per run via `QRESP_MAX_TOKENS` / a TOML config /
   the CLI, exactly like every other knob.
2. The OpenAI-compatible transport (`src/answer/local.py`) now receives
   `Config.max_tokens` through `LocalConfig.max_tokens` and threads it into every
   request; it used to hardcode `1024` in three method signatures and ignore the
   configured value entirely — which is why the recorded run_config said
   `max_tokens: 1024` while the rows truncated at 2048.

**Verification:** rows 20 and 21 complete on all three runs of the post-fix
`--repeats 3` eval (see EVAL.md current-baseline section).
