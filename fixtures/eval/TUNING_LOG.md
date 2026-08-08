# Tuning log

Records each deliberate tuning pass against the 20-question eval fixture: what was
changed, the before/after usable/needs-editing/wrong counts, and — most importantly —
what was tried and rejected, with why. A tuning log that only records adopted changes
hides exactly the reasoning a future session (or a client asking "why does the
threshold sit where it does") would need. Baseline (pre-tuning): **12 usable / 0
needs-editing / 8 wrong**, hand-scored against `fixtures/eval/questions.json`, model
`claude-sonnet-5`.

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
