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

  **Handling decision (made deliberately, not deferred):** without an explicit
  instruction, the system prompt's default behavior ("answer only from the supplied
  text") does not stop the model from *synthesizing* a single coherent answer out of
  two contradictory passages — that's the exact failure this label exists to catch,
  and it would happen silently. `generate.py`'s system prompt has an explicit rule
  (rule 8) requiring the model to state both conflicting claims with their sources
  rather than resolve them, and to self-report `self_confidence="low"`. There is
  deliberately no new status/confidence enum value for "contradictory" this slice —
  it's mapped onto the existing `polarity="partial"` + `self_confidence="low"` path,
  which already routes through the existing flag-for-human-review cell (yellow fill +
  comment) in `write_xlsx.py`. This is a real, if approximate, fit: a contradiction
  genuinely isn't a coherent complete answer either. Score these two fixtures
  leniently — the bar is "did it surface both claims and flag for review, not silently
  pick one," not an exact status match. A dedicated `AMBIGUOUS_EVIDENCE`-shaped status
  (distinct from ordinary `low`) is still a reasonable future refinement once more than
  two fixtures exist to design against — this slice deliberately does the smallest
  change that makes the two fixtures below actually test something, not the complete
  version of conflict handling.

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

  Three flavors here, and they want to be told apart even though all three get the
  same label:

  - **compound-question** partials — the question itself has multiple clauses (e.g.
    "do you encrypt data at rest and in transit, and how often are keys rotated"),
    evidence covers some clauses, not others. Fix direction: better question splitting.
  - **scope-mismatch** partials — a single-topic question broader than what's
    documented (e.g. "is the confidentiality, integrity, and availability of backup
    data ensured" when the corpus only documents backup availability/durability, not
    confidentiality or integrity of the backup data itself). Fix direction: better
    retrieval/corpus coverage.
  - **governance-gap** partials — the evidence confirms the operational practice but
    not the formal-policy layer the question actually asks about (e.g. "are crypto
    policies established, documented, approved, communicated, evaluated, and
    maintained" when the corpus documents that encryption is *applied*, with no
    mention of policy approval, review cadence, or ownership). Fix direction: neither
    of the above — this one is a real, distinct failure mode, not a bug to fix in this
    corpus. **Known corpus artifact**: this project's two evidence documents are
    operational policies with no governance/review-cadence content at all, so *every*
    CAIQ question touching policy lifecycle will land here for the same underlying
    reason. Two governance-gap slots in a 4-question `PARTIAL` bucket means you are
    deliberately measuring one corpus limitation twice, not two independent failure
    modes — accepted here because the gap itself is realistic (real evidence packs
    genuinely lack this), but don't read "half the PARTIAL bucket is governance-gap"
    as broad signal about partial-answer handling in general.

  Real CAIQ questions are often compound — three clauses joined by "and" covering
  distinct controls — and that's worth keeping some of deliberately, since it's
  realistic. But don't let compound-question artifacts dominate the `ANSWERED_PARTIAL`
  bucket either: if one flavor swamps the other two, the eval will look like it's
  measuring "partial answers" in general when it's really only measuring one cause.

**4. Otherwise** — the evidence directly and fully addresses the question as asked.

- If it confirms the control/practice exists → label `ANSWERED_AFFIRMS`.
- If it explicitly states the control/practice does **not** exist or is not offered
  (a documented negative, e.g. "shared accounts are prohibited") → label
  `ANSWERED_DENIES`. This is a real, fully-supported answer — never relabel a
  documented "no" as `NOT_FOUND` because the answer itself is negative.

  **`ANSWERED_DENIES` is structurally rare against CAIQ, and that's a finding about the
  instrument, not a gap to manufacture around.** Confirmed against real data, twice:
  scanning the full 262-question CAIQ v4.0.2 pool for a question our corpus's one
  negative statement ("shared accounts are prohibited") would answer found no clean
  match — the nearest ID (`IAM-13.1`, unique identification) is actually answered
  *affirmatively* by that same sentence. CAIQ almost always asks "does control X
  exist," not "is anti-pattern Y permitted," so a documented negative has nowhere to
  land in most of its questions regardless of what the evidence says. Target mix below
  reflects this: 1 `ANSWERED_DENIES` slot, not the 3 originally planned before seeing
  real question text. The one case is deliberately constructed — a real CAIQ question
  (`CEK-08.1`, "can CSCs manage their own encryption keys") the corpus did not
  originally address, answered by adding a single honest sentence to the existing
  encryption section rather than bending a question to fit. See the evidence corpus
  commit for exactly what was added and why.

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

- 6x `ANSWERED_AFFIRMS`: `BCR-08.1`, `IAM-08.1`, `IAM-13.1`, `BCR-03.1`, `SEF-06.1`,
  `UEM-13.1` — six distinct evidence sections, none shared by two questions (see the
  retrieval-verification table below; `IAM-02.1` was dropped from an earlier draft of
  this list — its question text is the same "established, documented, approved,
  communicated, ..., maintained" policy-lifecycle template as the two `governance-gap`
  `PARTIAL` cases, and this corpus has no governance-layer content to answer that
  template with, so it isn't a real `AFFIRMS`). `IVS-03.2` moved out of this bucket
  after hand-scoring the first baseline run — see the `ANSWERED_PARTIAL` note below.
- 1x `ANSWERED_DENIES` (dropped from an originally-planned 3 — see the rarity note above)
- 5x `ANSWERED_PARTIAL`: 1 compound (`STA-09.1`), 2 scope-mismatch (`BCR-08.2`,
  `IVS-03.2`), 2 governance-gap (`CEK-01.1`, `SEF-02.1`). `IVS-03.2` was reassigned
  here from `ANSWERED_AFFIRMS` during hand-scoring of the first baseline run (not
  bumped up front) — re-reading its evidence text ("all network traffic between
  clients and production services... internal service-to-service traffic within the
  production VPC") shows it covers production-internal traffic only, never
  cross-environment traffic, which is what "communications between environments"
  plausibly asks about. The baseline system's own answer got this right — stated the
  production-TLS facts, then explicitly flagged the cross-environment gap — which is
  what surfaced the label as too generous. Relabeled on the evidence, not because the
  system disagreed with the old label (see the eval baseline notes in the project
  README for the full reasoning). The 2 governance-gap slots still deliberately
  measure the same corpus limitation twice, as before.
- 6x `NOT_FOUND`, split as:
  - 4x easy (no nearby evidence at all — fully unrelated to anything the corpus covers)
  - 4x easy: `LOG-12.1`, `A&A-02.1`, `HRS-01.1`, `DSP-14.1` — verified against the real
    corpus (not assumed): none of these topics (physical access logging, independent
    audit assessments, background checks, sub-processor data disclosure) appear
    anywhere in the three evidence documents.
  - 2x **calibration** (a genuine near-miss: a control adjacent to but distinct from
    what the corpus documents, so retrieval lands close to `WEAK_MATCH_DISTANCE`
    rather than far past it): `BCR-08.3` ("can backups be restored appropriately for
    resiliency?" — corpus documents backup frequency/retention/location, never restore
    testing; rank 1, dist 0.321, right at the 0.3 threshold as intended) and `SEF-07.1`
    ("are processes for security breach notification defined?" — corpus documents
    incident triage/escalation, never notifying affected parties/regulators; rank 1,
    dist 0.399). Without these two, the eval only samples the easy end of the distance
    distribution and would report the threshold as well-calibrated when it's only been
    tested where it can't fail.

### Retrieval verification (mechanical, not by inspection)

Every `AFFIRMS`/calibration pick above was checked against the actual running
retriever (`HybridSearcher`, post `ingest`) — not assumed correct from reading the
source documents. That check caught real problems inspection missed, twice: `IAM-02.1`
looked fine by text but was a mislabel (see above), and an early pass had wrongly
called `UEM-13.1` a clean rank-1 match because *something* was retrieved from the same
document — it wasn't the section that actually supports the answer. Trust the query,
not the read.

| Control | Rank of the chunk that actually supports the answer | Vector distance | Note |
|---|---|---|---|
| `IVS-03.2` | 1 | 0.249 | clean |
| `BCR-08.1` | 1 | 0.249 | clean |
| `BCR-08.3` (calibration) | 1 | 0.321 | clean, right at threshold as intended |
| `SEF-07.1` (calibration) | 1 | 0.399 | clean *after* the boilerplate-chunk fix — the notice chunk was winning instead before that |
| `IAM-13.1` | 2 | 0.401 | buried — weak vector match even before reranking |
| `SEF-06.1` | 2 | 0.407 | buried — loses a close BM25 race to an unrelated chunk |
| `BCR-03.1` | 3 | 0.412 | buried — abstract policy phrasing doesn't embed near concrete RTO/RPO evidence |
| `UEM-13.1` | 4 | 0.346 | buried — top-1 was a different, wrong section entirely |
| `IAM-08.1` | 6 | 0.287 | buried worst, despite the *best* raw vector distance of this group |

**Kept deliberately, not re-picked.** Every buried row above has real, on-topic
evidence a human would find — that's what makes `ANSWERED_AFFIRMS` the correct label.
That current retrieval doesn't surface it is a system finding, not a fixture defect.
Swapping these for questions retrieval already handles well would tune the eval
questions to the system instead of the system to the questions — a benchmark that
can only ever pass. The baseline run is expected to score several of these wrong;
that's the harness doing its job.

Two distinct root causes are visible in the buried rows above, worth keeping separate
rather than reading as one generic "retrieval is imperfect" finding:

- **BM25/RRF reranking damage** (`IAM-08.1`, and to a lesser extent `SEF-06.1`):
  `IAM-08.1`'s correct chunk has the *best* vector distance of any buried row (0.287,
  under `WEAK_MATCH_DISTANCE`) but the hybrid reranking still buries it at rank 6 —
  keyword mismatch between the CAIQ phrasing and the evidence text outweighs a strong
  semantic match. Likely a quick, mechanical fix (e.g. reweighting RRF toward the
  vector rank, or a smarter BM25 tokenizer) once there's a slice to spend on retrieval
  tuning.
- **Embedding/chunking mismatch** (`BCR-03.1`, `UEM-13.1`): abstract, policy-register
  question phrasing doesn't embed close to concrete, operational evidence text (RTO/RPO
  numbers, an MDM remote-wipe sentence) even with no reranking involved. Not a quick
  parameter fix — likely needs either different chunking (shorter, more targeted
  chunks) or a different embedding model, and is worth its own investigation rather
  than folding into the BM25 fix above.
- 2x `AMBIGUOUS_EVIDENCE` — finalized in a dedicated pass (not bundled with the other
  18). Both are genuine contradictions: same control, two documents, both assertive,
  neither dated nor marked as draft/superseding (no way to resolve by recency or
  authority), and the two cases differ in kind from each other:

  - **`IAM-15.1`** ("secure management of passwords") — a **parameter** conflict.
    `access_control_policy.md` states passwords rotate every 180 days;
    `it_operations_standards.md` (new fixture doc) states every 90 days. Same
    quantity, same control, flatly incompatible.
  - **`IAM-14.1`** ("MFA for least-privileged user and sensitive data access") — a
    **scope/coverage** conflict, deliberately harder than the parameter case: the
    disagreement isn't a number but which accounts a control applies to.
    `access_control_policy.md` states all employee accounts require MFA;
    `it_operations_standards.md` states MFA applies only to administrative/privileged
    accounts, with standard accounts authenticating via SSO instead. A model that
    isn't careful can read these as compatible ("admins are a subset of all
    employees") when they're actually mutually exclusive — doc A claims *every*
    account, doc B explicitly carves standard accounts out. This is the case most
    likely to get smoothed into a false-confident single answer, which is exactly why
    it's here.

  `IAM-14.1` was originally slated for `ANSWERED_AFFIRMS`; reassigned here because its
  real question text ("MFA for least-privileged user and sensitive data access") fits
  the scope-conflict case better than a plain affirmation, and because it was one of
  three `ANSWERED_AFFIRMS` questions all landing on the same short Authentication
  paragraph — pulling it out helped fix that clustering. The `ANSWERED_AFFIRMS`
  backfill (`IAM-08.1`, `IAM-13.1`, `BCR-03.1`, `SEF-06.1`, `UEM-13.1`, plus the
  originally-clean pick `BCR-08.1`) lands on six distinct evidence sections across all
  three documents — see the retrieval-verification table below. (`IVS-03.2` was also
  originally in this backfill and retrieves cleanly, but was later moved to
  `ANSWERED_PARTIAL` during baseline hand-scoring — a labeling correction unrelated to
  retrieval, see the `ANSWERED_PARTIAL` section above.)

  `it_operations_standards.md` must contain enough unrelated real-looking content
  around the two conflicting paragraphs that retrieval isn't trivially handed only the
  contradiction — a document that's nothing but the two conflicting sentences makes
  the test easier than a real evidence pack would be.

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
`notes` is where the calibration/compound-vs-scope-mismatch/governance-gap flavor and
any labeling rationale goes.

**Licensing: quote a small selection, never commit the full source pool.** CSA's terms
prohibit redistributing CCM/CAIQ (the one exception — a CSP redistributing its own
filled-out copy — doesn't apply here), but permit quoting portions under fair use with
attribution to CSA. The ~20 selected questions with control IDs and attribution are
defensible fair-use quotation; a near-complete reproduction of the instrument (e.g. the
full ~260-question extracted pool used to select from) is not. Do the full-pool
extraction and selection in a scratch location outside the repo, and never commit
anything beyond the selected subset. (No AWS or other vendor's *answers* should ever
appear anywhere in this repo either, extracted or otherwise — that's a separate,
answer-contamination reason to keep source extraction scoped to question text + ID
only, verified before selecting.)
