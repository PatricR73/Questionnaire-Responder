# Eval fixture labeling guide

This document defines the expected-outcome label for each (question, evidence corpus)
pair in the slice-2 eval set, and the target mix of labels for the fixture set as a
whole. Write labels against these definitions **before** running the pipeline against
the questions — see the anti-mirror methodology in the project notes: labels are
ground truth and must not be inferred from, or reconciled with, what the system
happens to output.

This is a labeling document, not code. `AnswerPolarity` (`affirms`/`denies`/`partial`)
in `src/answer/answerer.py` is what the *system* outputs for a processed row; the
labels below are the *expected* outcome a human decides in advance. They share
vocabulary where the concepts line up, but `AMBIGUOUS_EVIDENCE` below has no system-side
equivalent yet (see the note under that label).

## The four cases that get confused, and how to tell them apart

Ask, in order, for each (question, evidence corpus) pair:

**1. Does the evidence say anything at all about the actual subject of the question?**

Not "is there a topically-nearby passage" — does it speak to what was actually asked.
If a human reading only the retrieved evidence could not write a single correct
sentence in response (not even a hedged, partial one), the answer is no.

- If **no** → label `NOT_FOUND`. This includes true silence (topic never mentioned
  anywhere in the corpus) **and** the distractor case: evidence about a related but
  different control (e.g. the question asks about backup frequency, the only nearby
  evidence describes encryption at rest). A near-miss retrieval is still a miss. Do
  not label this `PARTIAL` — "on-topic but incomplete" and "off-topic but nearby" are
  different failure modes with different reviewer actions (see below), and collapsing
  them erases exactly the distinction the confidence cross-check exists to catch.

  Two flavors of `NOT_FOUND`, and the fixture set needs both — see the target mix
  below: an **easy** case (evidence has nothing topically nearby at all — a fully
  off-topic CAIQ question against this corpus will typically retrieve at ~0.44+
  distance, comfortably past `WEAK_MATCH_DISTANCE = 0.3`), and a **calibration** case
  (evidence has a genuine near-miss — a control that's adjacent but distinct from what
  was asked, retrieving close to the threshold, the way the encryption-in-transit
  question's real match at 0.163 sat close to the encryption-at-rest distractor at
  0.177). Only the calibration case actually exercises whether the threshold does
  anything; the easy case would pass with almost any threshold value.

**2. Does the evidence contradict itself on this question** (two passages, or two
documents, giving incompatible answers to the same control)?

- If **yes** → label `AMBIGUOUS_EVIDENCE`. This is a data-quality problem, not a
  coverage problem — the fix is fixing the evidence base, not writing a better prompt.
  The system has no dedicated handling for this today (`self_confidence` has no
  "contradictory" value); include these questions anyway as a stress test, and score
  the system leniently for now — the bar is "did it avoid confidently asserting one
  side," not an exact status match. Do not rush to implement handling for this: two
  fixtures of contradictory evidence is not enough signal to design a resolution
  strategy, and the honest v1 behavior is probably "flag for a human" rather than the
  system picking a side on its own. Flag this gap for a future slice instead of
  retrofitting a code-level fix while writing labels.

**3. Does the question have multiple explicit parts, or ask about a broader scope than
the evidence covers** (compound question: "do you do X, and how often is Y done" where
evidence only covers X; or a single-topic question where evidence covers only a strict
subset of what's asked, e.g. "how do you protect customer data" when evidence only
addresses encryption, not access control or backups)?

- If **yes**, and evidence is on-topic for the part(s) it does cover → label
  `ANSWERED_PARTIAL`. The distinguishing test from `NOT_FOUND` is that a human really
  could write a correct (if incomplete) sentence or two from this evidence — the gap
  is coverage, not relevance. Reviewer action: verify the system's answer explicitly
  names what's unaddressed rather than silently answering only the covered part.

  Two flavors here too, and they want to be told apart even though both get the same
  label: **compound-question** partials (the question itself has multiple clauses,
  e.g. "do you encrypt data at rest and in transit, and how often are keys rotated" —
  evidence covers some clauses, not others) versus **scope-mismatch** partials (a
  single-topic question broader than what's documented, e.g. "how do you protect
  customer data" when only encryption is documented). Real CAIQ questions are often
  compound — three clauses joined by "and" covering distinct controls — and that's
  worth keeping some of deliberately, since it's realistic. But don't let compound-
  question artifacts dominate the `ANSWERED_PARTIAL` bucket: the two failure modes
  want different fixes (compound → better question splitting; scope-mismatch → better
  retrieval/corpus coverage), and if the bucket fills with one flavor the eval will
  look like it's measuring "partial answers" in general when it's really only
  measuring one specific cause. Aim for roughly half and half of the 4 `PARTIAL` slots.

**4. Otherwise** — the evidence directly and fully addresses the question as asked.

- If it confirms the control/practice exists → label `ANSWERED_AFFIRMS`.
- If it explicitly states the control/practice does **not** exist or is not offered
  (a documented negative, e.g. "shared accounts are prohibited") → label
  `ANSWERED_DENIES`. This is a real, fully-supported answer — never relabel a
  documented "no" as `NOT_FOUND` because the answer itself is negative.

## Summary table

| Label | Evidence relevant to the actual question? | Complete? | Consistent? |
|---|---|---|---|
| `ANSWERED_AFFIRMS` | yes | yes | yes |
| `ANSWERED_DENIES` | yes | yes | yes |
| `ANSWERED_PARTIAL` | yes | no (covers some but not all) | yes |
| `NOT_FOUND` | no (incl. off-topic/distractor) | n/a | n/a |
| `AMBIGUOUS_EVIDENCE` | yes | — | no (self-contradictory) |

## Target label mix for the first fixture set (20 questions)

Real questionnaire questions (CAIQ/VSAQ) cover a far wider control surface than a
two-document synthetic corpus, so pulling questions unfiltered will skew heavily
toward `NOT_FOUND` — that measures abstention almost exclusively and says nothing
about retrieval or answer quality. Target mix, decided before fetching:

- 5x `ANSWERED_AFFIRMS`
- 3x `ANSWERED_DENIES`
- 4x `ANSWERED_PARTIAL`
- 6x `NOT_FOUND`, split as:
  - 4x easy (no nearby evidence at all — fully unrelated to anything the corpus covers)
  - 2x **calibration** (a genuine near-miss: a control adjacent to but distinct from
    what the corpus documents, so retrieval lands close to `WEAK_MATCH_DISTANCE`
    rather than far past it). Concretely: pick CAIQ/VSAQ questions whose topic the
    corpus covers *adjacently but not actually* — e.g. a question about vulnerability
    disclosure when the corpus documents patch management, or a question about
    subprocessor audits when the corpus documents vendor onboarding. Without these
    two, the eval only samples the easy end of the distance distribution and would
    report the threshold as well-calibrated when it's only been tested where it can't
    fail.
- 2x `AMBIGUOUS_EVIDENCE`

To hit this: select CAIQ/VSAQ questions to fit the mix, and where there aren't enough
naturally-answerable real questions for the corpus as it stands, **expand the evidence
corpus with new synthetic documents covering additional control areas** (e.g. vendor
risk management, subprocessor management, physical security, data retention/deletion,
pentesting cadence) rather than rewriting or cherry-picking questions to fit the two
documents that already exist. Bending the corpus toward real question coverage is
correct; bending real questions toward the corpus reintroduces the mirror problem this
whole methodology exists to avoid.

## Sourcing questions: CAIQ/VSAQ

**Parse the actual source file; do not scrape a summary/paraphrase page.** CSA
distributes CAIQ as an xlsx download — question text lives in a specific column
alongside a control ID and domain grouping. A webpage that summarizes or paraphrases
CAIQ content is one hop further into the mirror problem this methodology exists to
avoid: the point of an external source is exact human-written phrasing, and a
paraphrase (even a human-written one, even not written by this project) throws that
away. Download the real xlsx and parse it the same way `questionnaire/parse_xlsx.py`
would — don't transcribe from memory or from a page that already reworded it. VSAQ is
JSON-backed (a web app), which is more directly parseable but has its own structure to
read correctly rather than skim.

**Record provenance per question.** Every fixture question carries its source control
ID (e.g. CAIQ `IAM-02`) alongside the question text. Two reasons: it's the proof the
question wasn't generated, and it's how a bad-fit question gets traced back to what
was actually pulled instead of leaving it ambiguous whether the text was edited along
the way. Fixture row shape: `{source_id, question_text, expected_label, notes}` —
`notes` is where the calibration/compound-vs-scope-mismatch flavor and any labeling
rationale goes.
