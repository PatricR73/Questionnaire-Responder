# What building a no-fabrication RAG pipeline taught us about saying "I don't know"

*A case study from the Questionnaire Responder project — how a vendor-security-questionnaire
tool learned, from its own measured data, when to keep its mouth shut.*

---

## The problem

A B2B company selling to enterprises gets handed spreadsheets of security questions:
"Do you encrypt data at rest?" "How often do you review who has access to what?"
"Can you delete our data on request?" Before a deal closes, someone has to answer
every row correctly, from the company's own security documentation, usually in a
week or less. That is what this project automates: read the policies, draft a
first-pass answer to every row, cite the exact passage each answer came from, and
leave the human review step in charge of the final send.

The naive version of this — throw a generic RAG system at the policy folder and let
a large language model write the answers — fails in a way that is specific to this
domain and worse than a typo. On a vendor security questionnaire, a wrong answer is
not a spelling mistake: it is a **written representation to a prospective customer
about what your company actually does**. "Yes, we run quarterly penetration tests"
is a statement that can end up quoted in a contract. An LLM that fills the gap with
what a security questionnaire *usually* says is not making a small error; it is
fabricating a control the organization has never documented, and the customer who
relied on it has a legal claim, not a bug report. So the project's central design
constraint became: **never assert a security control the evidence does not
document** — abstain, visibly and honestly, rather than guess.

The project's other founding bet is that this constraint is commercially
defensible, not just safe. An honest gap is a cheap problem: someone writes the
missing policy or answers the row by hand, and the customer sees a vendor that
knows what it does and does not do. A confident fabrication is an expensive
problem, discovered only after it has been relied on. In fact, the abstentions
turned out to be a product of their own: the rows the evidence cannot answer are,
collectively, a documentation gap analysis — "your policy set does not document:
business continuity test cadence, sub-processor inventory, encryption key
rotation, incident notification SLA." The tool's honest gaps became a deliverable.

## The two-layer no-fabrication design

The constraint is enforced twice, independently, on purpose — one mechanism trusting
itself is exactly how this kind of guarantee quietly erodes.

First, the system prompt instructs the model that a question with no supporting
evidence must return an empty, unsupported answer — and that this is the *correct,
expected* output for many rows, not a failure. The model is told, in so many words,
that "not found" is a win.

Second — and this is the layer that makes the first one worth anything — the
pipeline does not trust that instruction. A separate module cross-checks that every
sentence the model cites as support appears **byte-for-byte** (after whitespace
normalization) in the retrieved evidence text. A citation that does not check out
forces the answer back down to "not found," regardless of what the model claims
about itself. The same two-layer pattern is applied to vocabulary selection: the
schema constrains the model to the sheet's actual value list, and a runtime
assertion backstops it.

A third layer, behind a flag, closes the gap the first two cannot: grounding
catches *invented* citations, but it is weaker against *over-inference from real
ones* — quoting a real sentence verbatim is not the same as the answer following
from it. The entailment check sends only the drafted answer and its citations to a
separate model and asks whether every factual claim is actually stated in them. It
can only downgrade confidence, never upgrade it.

## The eval-before-tuning discipline

The project's real discipline is not the architecture — it is that **every change
is measured against a known baseline before it is adopted**. The eval set is 24
questions: 20 quoted verbatim from the real CSA CAIQ v4.0.2 instrument, hand-labeled
against a synthetic evidence corpus *before* the pipeline ever ran against them,
plus 4 adversarial questions written specifically to try to make the tool fabricate.
The scoring is structural (status and polarity vs. expected label), and the harness
shells out to the real CLI, so the thing being measured is the thing a user would
actually run. Two hard rules govern every tuning pass: change one thing, run the
full set twice to check variance, and treat "a NOT_FOUND question starts answering"
as automatically disqualifying regardless of what it does to the total.

## The centrepiece: a tuning pass we declined to make

The most instructive episode was not a change that worked. After the first baseline,
six of the remaining wrong answers shared one visible cause: the model had answered
correctly, but the confidence threshold had silently discarded the answer because
the best retrieved chunk's cosine distance exceeded the cutoff. The obvious fix was
to raise the threshold. The numbers said no.

The threshold-finding table in the project's eval log tells the story in eight rows.
One of the six questions that **must always abstain** — a deliberately-built trap
question whose answer the evidence genuinely does not support — sits at distance
0.321, and it is only abstaining correctly *because* 0.321 exceeds the current
threshold of 0.3. Every wrong case a higher threshold could rescue sits above that:
0.323, 0.340, 0.359, 0.384, 0.412. There is no value that fixes any of them without
also flipping the trap question into confidently asserting a control its evidence
does not support — which the project treats as automatically disqualifying. The
threshold is a single knob, and this corpus has no setting for it. The tuning pass
was rejected, and the reasoning — not just the decision — was committed to the log.

Most engineering teams cannot show you a decision they declined to make on the
evidence. That is precisely why this one is the most valuable page in the project:
it is a demonstration, in public, of a team that let data veto a plausible fix. The
threshold stays where it is because the measurement said the cost of moving it is
the one failure mode the product exists to prevent.

## Three things that turned out differently than expected

**1. The threshold re-derivation failed, honestly.** When the store's distance
metric changed, an earlier draft of the threshold conversion assumed the old
distances were L2 distances of unit-normalized vectors and converted 0.3 to 0.045
using 0.3²/2. The measured sweep showed the old numbers had never lived in that
space, and 0.045 flagged every retrieved chunk as weak. The conversion was reverted,
and the log records both the attempted derivation and why it was wrong. The lesson:
a number that reproduced a decision boundary was kept for exactly that reason, not
because anyone could re-derive it from first principles.

**2. The adversarial subset caught the project overstating its own claim.** The
first published pass reported two fabrications in the adversarial set — the exact
failure the tool is built to prevent. Re-reading the real captured answers against
the evidence overturned both: one was a mislabel (the evidence verbatim documents
the claimed control; the label was wrong, not the model), and one was a
status-mapping defect (the answer text abstains correctly, but the structured output
reported `supported=true`, so the row was recorded as answered). The headline claim
was corrected in public — the measured number changed from "two fabrications" to
"no confirmed textual fabrication; a labeler error and a status-flag defect." The
subset did its job even though the first pass misread the outcome: it forced the
project's own labeler error into the open, which is the discipline the harness
exists to enforce.

**3. Two diagnoses were overturned by instrumenting instead of inferring.** Three
wrong cases were initially logged as "the model saw the evidence and chose to
self-abstain anyway" — a generation-prompt problem. Instrumenting the actual
self-confidence and support values showed the model had answered correctly every
time; the confidence threshold was silently discarding good answers afterward.
There was no prompt bug to fix. The "over-caution" diagnosis was an inference from
an aggregate status that collapses two genuinely different causes into one
observable outcome — and it was wrong.

## The honest state so far

Measured on the 24-question set (Claude, superseded 2026-08-18): **15/24 structural
match**, zero confirmed textual fabrications on re-examination, 9 rows wrong for
named, verified reasons (7 honest abstentions + 2 other causes), with a 95% Wilson
interval wide enough that nobody should mistake 15/24 for a finished instrument.
These are the Claude-era measurements: the baseline provider moved to DeepSeek
(`deepseek-v4-flash`) on 2026-08-18, and the DeepSeek baseline is pending
re-measurement — see the
[EVAL.md current-baseline section](../EVAL.md#current-baseline-deepseek-deepseek-v4-flash--measurement-pending).
The confidence-threshold
problem remains unsolved at this corpus size. The answer library — the feature that
makes the second questionnaire cheaper than the first — is built behind a flag,
with its real-provider delta still to be measured. A fully on-premise provider was
measured: a small local model scored 9/24 and, more importantly, violated the
abstention constraint on two rows — published precisely so a buyer can see that
"fully local" has a measured accuracy cost and an abstention risk. Open issues
track the threshold redesign, real compound-question splitting, a larger eval
corpus, and a status-mapping defect. Every number, every rejected pass, and every
wrong case is in the repo. That is the point: the most credible thing this project
produces is an honest record of where it falls short.

---
*The code, the eval, and every log referenced above are public in the
[Questionnaire Responder repository](https://github.com/PatricR73/Questionnaire-Responder).*
