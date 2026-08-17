# Design

This document carries the design rationale that used to live in the README: the
no-fabrication constraint, what is deliberately not built yet and why, and the v2
priority order. The README keeps the what/why, the results, quickstart, usage, and
links — the reasoning moved here so a reader reaches the code faster without
losing any of it.

## The core design goal: never assert a control the evidence does not document

A wrong answer on a security questionnaire isn't a typo — it's a written
representation to a prospective customer about what the company actually does. If the
tool asserts a control that doesn't exist ("yes, we run quarterly penetration tests")
because that's what a security questionnaire *usually* says, that's not a bug the
customer will shrug off; it's a false statement in a document that may end up
referenced in a contract. So the tool is built around one non-negotiable goal: **never
assert a security control the organization has not documented.** If the
evidence base doesn't support an answer, the tool does not guess, does not extrapolate
from "typical" industry practice, and does not fill the gap with a plausible-sounding
claim. That row is left with the literal marker `NOT FOUND IN PROVIDED DOCUMENTS`
instead. An honest gap is a cheap problem — someone writes the missing policy or
answers the row by hand. A confident fabrication is an expensive one, discovered only
when it's already been relied on.

**Goal, not guarantee — with a measured failure rate.** The README's results table
is the honest accounting, and the adversarial subset (four questions whose answers
are plausibly implied by the evidence but, at labeling time, judged not documented)
exists specifically to stress the goal. The first published pass reported two
fabrications; re-reading the real captured answers against the evidence corrected
that — one was a mislabel (the evidence does document the claim; the label was fixed)
and one is a status-mapping defect (the answer text abstains correctly). No confirmed
textual fabrication — a claim beyond the cited evidence — has been found so far. That
is a measured outcome, not a guarantee: the subset is small, and the whole point of
recording the number is that the next run can change it.

The goal is enforced by independent layers, on purpose — one mechanism trusting
itself is exactly how this kind of guarantee quietly erodes:

1. The system prompt in `src/answer/generate.py` instructs Claude that no-evidence
   questions must return an empty, unsupported answer — this is documented as the
   *expected*, correct output for many questions, not a failure.
2. `src/answer/confidence.py` does not trust that instruction blindly: it checks that
   every sentence Claude cites as support actually appears verbatim in the evidence
   text that was retrieved. A citation that doesn't check out forces the answer back
   down to "not found," regardless of what Claude claims about itself.

These two layers are strong against *invented* citations. Their honest limitation —
the reason the adversarial subset exists — is that grounding alone is weaker against
*over-inference from real citations*: quoting a real sentence verbatim is not the
same as the answer following from it. The third layer (`src/answer/entailment.py`,
A1, behind the `entailment_check` flag) closes that gap: a separate cheap call
receiving only the answer and its citations judges whether every factual claim is
stated in them. It is off by default until its false-positive cost is measured.

The same two-layer pattern is applied to vocabulary selection: the per-request
structured-output schema constrains `vocab_selection` to the sheet's actual value
list (constrained decoding), and `AnthropicAnswerer` asserts membership at runtime —
a non-member value is dropped and the row downgraded to "low" rather than written.

**Every output requires human review before it goes to a customer.** High-confidence
answers are written directly into the workbook, but "high confidence" describes the
retrieval and citation-grounding checks that produced the draft — it is not a
substitute for a person reading the answer. Low-confidence rows are flagged visibly in
the workbook (yellow fill + comment) specifically to be reviewed. Rows that errored
during processing are flagged in red and must be re-run, not trusted as-is. A row
with no supporting evidence gets a distinct neutral fill and a comment saying it
needs a human answer — in a 300-row sheet an honest abstention must not look like a
real answer.

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
wishlist. Each item tracks as a GitHub issue (milestone v0.2.0); the prose below is
the narrative, the issues are the work items:

1. **A confidence signal that isn't a single flat distance threshold** — [issue #3](https://github.com/PatricR73/Questionnaire-Responder/issues/3).
   This is what 6 of the 7 remaining wrong answers trace back to, and the eval data
   shows conclusively that no value of `WEAK_MATCH_DISTANCE` fixes it for this
   corpus (see the threshold finding in [`EVAL.md`](../EVAL.md)) — the "weak but
   real" and "genuinely absent" distance clusters overlap. Likely needs a larger,
   more varied corpus to find a real separation point, a different signal entirely
   (e.g. distance of the specific cited chunk rather than the best distance across
   everything retrieved — the per-chunk grounding work makes that identity
   available), or both. The A1 entailment layer and the P11 rerank score are the
   first non-distance signals in place.
2. **Real compound-question splitting** — [issue #4](https://github.com/PatricR73/Questionnaire-Responder/issues/4).
   The other remaining wrong case, and the only one not blocked by the threshold
   problem described in [`EVAL.md`](../EVAL.md) — independently fixable now; the
   pipeline's aggregation contract (one row-level result, weakest confidence across
   parts) is already in place for it.
3. **Expand the eval corpus** — [issue #5](https://github.com/PatricR73/Questionnaire-Responder/issues/5).
   Both the threshold redesign above and any future retrieval/prompt change need a
   bigger, more diverse fixture set to measure against than 24 questions over 3
   documents; this baseline is deliberately small and honest about that, not a
   finished instrument.
4. **Feedback loop** (write approved/edited answers back into the evidence base) —
   [issue #2](https://github.com/PatricR73/Questionnaire-Responder/issues/2), now
   that the review UI exists to produce that signal.
5. **PDF fixture validation**, and CSV/other questionnaire formats — [issue #9](https://github.com/PatricR73/Questionnaire-Responder/issues/9);
   lower priority because no real user of this tool has hit either gap yet;
   speculative until then.

Open findings from the first adversarial run also track as issues: the ADV-04
status-mapping defect ([issue #6](https://github.com/PatricR73/Questionnaire-Responder/issues/6)),
the entailment layer's pending live eval ([issue #7](https://github.com/PatricR73/Questionnaire-Responder/issues/7)),
and the demo's missing inline player on github.com ([issue #8](https://github.com/PatricR73/Questionnaire-Responder/issues/8)).
