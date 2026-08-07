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

**2. Does the evidence contradict itself on this question** (two passages, or two
documents, giving incompatible answers to the same control)?

- If **yes** → label `AMBIGUOUS_EVIDENCE`. This is a data-quality problem, not a
  coverage problem — the fix is fixing the evidence base, not writing a better prompt.
  The system has no dedicated handling for this today (`self_confidence` has no
  "contradictory" value); include these questions anyway as a stress test, and score
  the system leniently for now — the bar is "did it avoid confidently asserting one
  side," not an exact status match. Flag this gap for a future slice rather than
  trying to retrofit a code-level fix while writing labels.

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
- 6x `NOT_FOUND`
- 2x `AMBIGUOUS_EVIDENCE`

To hit this: select CAIQ/VSAQ questions to fit the mix, and where there aren't enough
naturally-answerable real questions for the corpus as it stands, **expand the evidence
corpus with new synthetic documents covering additional control areas** (e.g. vendor
risk management, subprocessor management, physical security, data retention/deletion,
pentesting cadence) rather than rewriting or cherry-picking questions to fit the two
documents that already exist. Bending the corpus toward real question coverage is
correct; bending real questions toward the corpus reintroduces the mirror problem this
whole methodology exists to avoid.
