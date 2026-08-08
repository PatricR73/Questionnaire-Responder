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
