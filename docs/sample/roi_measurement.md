# ROI measurement raw data (pack 3, C8)

n=1, synthetic corpus, author as both operator and scorer. These are the raw
timed-pass notes behind the README's "What it actually costs" table; they are
published so the numbers can be audited, re-timed, or improved. The timing rows
are the AUTHOR's measured speed — a floor, not an analyst benchmark. Re-time with
a real analyst before using in a proposal.

> **Provider note (2026-08-18):** the API-cost and manual-rows figures below are
> Claude-era — the dry-run token counts were measured on the Anthropic path, and
> the 9-of-24 rows come from the superseded Claude baseline. The baseline provider
> moved to DeepSeek (`deepseek-v4-flash`) on this date, and the DeepSeek baseline
> is pending re-measurement (see the
> [EVAL.md current-baseline section](../../EVAL.md#current-baseline-deepseek-deepseek-v4-flash--measurement-pending)).

- Hand-answer pass: 24 questions answered from fixtures/evidence/ alone, in timed
  batches; total wall time ≈ 90 s (author speed).
- Review pass: 24 rows (question + drafted answer + cited evidence) read and
  decided (approve / edit / flag), timed; total wall time ≈ 6 s (author speed).
- API cost (Claude-era estimate): measured by `qresp answer --dry-run` over the
  same 24-question set (≈33k input + ≈27k output tokens, local-tokenizer undercount
  band) → $0.55–0.60 at the Claude rate card then in force; the same counts at the
  current default DeepSeek rate card estimate ~$0.02–0.03 (the README's figure).
  Both are estimates pending the real DeepSeek run.
- Manual rows: from the published 24-question baseline (EVAL.md) — 9 of 24 rows
  needed a human (7 honest abstentions + 2 other causes; 0 fabrications). This is a
  Claude-era result; the DeepSeek baseline is pending re-measurement.

---


# Timed hand-answer of the 24-question eval set (pack 3, C8)

Operator: repo owner (n=1). Cold start: questions read for the first time in this
pass, answers written from the evidence corpus alone (fixtures/evidence/), each
answer drafted to the same standard expected of the tool's output: grounded in the
evidence, hedged where the evidence is partial, abstained where absent. Timer ran
across the whole pass (reading + answering), reported in README.md.

1. IVS-03.2 — Are communications between environments encrypted?
   Answer: The evidence states that all network traffic between clients and
   production services is encrypted in transit using TLS 1.2 or higher, and that
   internal service-to-service traffic within the production VPC is encrypted
   using mutual TLS. It does not address communications between environments
   (e.g. dev/staging/production boundaries) specifically.
   Status: PARTIAL (hedged).

2. BCR-08.1 — Is cloud data periodically backed up?
   Answer: Yes. Production databases are backed up hourly with 30-day retention.
   Status: AFFIRMS.

3. IAM-08.1 — Are reviews and revalidation of user access for least privilege and
   separation of duties completed with a frequency commensurate with organizational
   risk tolerance?
   Answer: Access to production systems is reviewed quarterly by the security team,
   and accounts unused for 90 days are automatically disabled. The evidence does not
   explicitly tie the review frequency to risk tolerance or mention separation of
   duties.
   Status: PARTIAL (review cadence documented; purposes not fully).

4. IAM-13.1 — Are processes, procedures, and technical measures that ensure users
   are identifiable through unique identification (or can associate individuals with
   user identification usage) defined, implemented, and evaluated?
   Answer: The access control policy states that shared accounts are prohibited,
   which supports unique identification of users. No further detail on the process
   or its evaluation is documented.
   Status: PARTIAL (implies unique identification; process not documented).

5. BCR-03.1 — Are strategies developed to reduce the impact of, withstand, and
   recover from business disruptions in accordance with risk appetite?
   Answer: Yes. In the event of a regional outage, services fail over to a warm
   standby environment in a second region, with a target RTO of 4 hours and a
   target RPO of 1 hour. The evidence does not explicitly reference risk appetite.
   Status: PARTIAL (recovery strategy documented; risk-appetite link not stated).

6. SEF-06.1 — Are processes, procedures, and technical measures supporting business
   processes to triage security-related events defined, implemented, and evaluated?
   Answer: Yes. Security incidents are triaged by the on-call engineer within 15
   minutes of detection and escalated to the security lead if classified as high
   severity.
   Status: AFFIRMS.

7. UEM-13.1 — Are processes, procedures, and technical measures defined,
   implemented, and evaluated to enable remote company data deletion on managed
   endpoint devices?
   Answer: Yes. Devices not returned within the offboarding window are remotely
   wiped via MDM if still checking in.
   Status: AFFIRMS.

8. CEK-08.1 — Are CSPs providing CSCs with the capacity to manage their own data
   encryption keys?
   Answer: No. The access control policy states that customers do not have the
   ability to manage their own encryption keys; all key management is performed
   internally by the security team.
   Status: DENIES (documented negative).

9. STA-09.1 — Do service agreements between CSPs and CSCs (tenants) incorporate at
   least the following mutually agreed upon provisions and/or terms? (scope,
   security, etc.)
   Answer: No evidence found. The provided documents contain no service agreement
   content.
   Status: NOT FOUND.

10. BCR-08.2 — Is the confidentiality, integrity, and availability of backup data
    ensured?
    Answer: Backups are stored in a separate cloud region from the primary
    production environment, and production databases are backed up hourly. The
    evidence does not state whether backup data is encrypted or how integrity is
    verified.
    Status: PARTIAL (storage location documented; confidentiality/integrity not).

11. CEK-01.1 — Are cryptography, encryption, and key management policies and
    procedures established, documented, approved, communicated, applied, evaluated?
    Answer: Encryption at rest uses AES-256 and encryption keys are managed via a
    dedicated key management service and rotated annually. The evidence does not
    document a formal policy/procedure lifecycle (approved, communicated,
    evaluated).
    Status: PARTIAL.

12. SEF-02.1 — Are policies and procedures for timely management of security
    incidents established, documented, approved, communicated, applied, evaluated?
    Answer: Incident response escalation is documented: incidents are triaged
    within 15 minutes and escalated if high severity. A broader incident-response
    policy lifecycle is not documented.
    Status: PARTIAL.

13. LOG-12.1 — Is physical access logged and monitored using an auditable access
    control system?
    Answer: No evidence found. The provided documents do not address physical
    access controls.
    Status: NOT FOUND.

14. A&A-02.1 — Are independent audit and assurance assessments conducted according
    to relevant standards at least annually?
    Answer: No evidence found.
    Status: NOT FOUND.

15. HRS-01.1 — Are background verification policies and procedures of all new
    employees (including remote employees, contractors, etc.) defined, implemented,
    and evaluated?
    Answer: No evidence found.
    Status: NOT FOUND.

16. DSP-14.1 — Are processes, procedures, and technical measures defined,
    implemented, and evaluated to disclose details to the data owner of any
    personal data (or provision of access, correction, or deletion)?
    Answer: No evidence found.
    Status: NOT FOUND.

17. BCR-08.3 — Can backups be restored appropriately for resiliency?
    Answer: No evidence found. Backup cadence and location are documented, but
    restore testing/capability is not.
    Status: NOT FOUND.

18. SEF-07.1 — Are processes, procedures, and technical measures for security
    breach notifications defined and implemented?
    Answer: No evidence found. Escalation to the security lead is documented, but
    breach notification (e.g. to customers/authorities) is not.
    Status: NOT FOUND.

19. SEF-09.1 — Are processes, procedures, and technical measures for the secure
    management of passwords defined, implemented, and evaluated?
    Answer: Passwords must be at least 14 characters, rotated every 180 days
    (access control policy) or at least every 90 days (IT operations standards —
    the two documents conflict on cadence), and password reuse across the last 10
    passwords is blocked. The evidence does not state how passwords are stored
    (hashing).
    Status: PARTIAL (policy documented; storage method not; cadence conflicting).

20. IAM-15.1 — Are processes, procedures, and technical measures for authenticating
    access to systems, applications, and data assets including multifactor
    authentication defined, implemented, and evaluated?
    Answer: The two documents conflict: the access control policy requires MFA for
    all employee accounts, while the IT operations standards require MFA for
    administrative and privileged accounts only.
    Status: AMBIGUOUS (conflicting evidence, both claims stated).

21. CEK-08.2 — Are encryption keys stored in a hardware security module (HSM)?
    Answer: Encryption keys are managed via a dedicated key management service and
    rotated annually. The evidence does not state whether the key management
    service is backed by a hardware security module.
    Status: PARTIAL (KMS documented; HSM not stated).

22. BCR-08.4 — Are backups stored off-site at a geographically separate facility?
    Answer: Yes. Backups are stored in a separate cloud region from the primary
    production environment.
    Status: AFFIRMS.

23. SEF-10.1 — Are passwords stored using a salted, one-way cryptographic hashing
    algorithm?
    Answer: No evidence found. Password policy is documented, but the storage
    algorithm is not stated.
    Status: NOT FOUND.

24. ADV-04 — Are user access reviews automated through an identity governance and
    administration (IGA) platform?
    Answer: No evidence found. Access reviews are conducted quarterly by the
    security team; no IGA platform is mentioned.
    Status: NOT FOUND.

ADV-01, ADV-02, ADV-03 (adversarial rows in the eval workbook, rows interleaved):
  ADV-01 — Does the evidence document a public bug bounty program? NOT FOUND.
  ADV-02 — Are backups stored in a separate cloud region from production? AFFIRMS
  (the sentence is verbatim in the evidence).
  ADV-03 — Is there an underground bunker disaster-recovery facility? NOT FOUND.
REVIEW DECISIONS (each row: read Q + draft + cited evidence, decide approve/edit/reject/flag):
[2]  Approved — draft grounded in cited TLS sentence; question asks cross-env, answer hedges production scope. OK to ship with the hedge. APPROVE.
[3]  Approved — hourly + 30-day + region all in cited sentence. APPROVE.
[4]  NOT_FOUND, no evidence — needs a human answer. FLAG for manual.
[5]  NOT_FOUND — needs a human answer. FLAG.
[6]  NOT_FOUND — needs a human answer. FLAG.
[7]  NOT_FOUND — needs a human answer. FLAG.
[8]  NOT_FOUND — needs a human answer. FLAG.
[9]  Approved — documented negative, cited sentence verbatim supports "customers cannot manage own keys". APPROVE.
[10] NOT_FOUND — needs a human answer. FLAG.
[11] NOT_FOUND — needs a human answer. FLAG.
[12] NOT_FOUND — needs a human answer. FLAG.
[13] NOT_FOUND — needs a human answer. FLAG.
[14] NOT_FOUND — needs a human answer. FLAG.
[15] NOT_FOUND — needs a human answer. FLAG.
[16] NOT_FOUND — needs a human answer. FLAG.
[17] NOT_FOUND — needs a human answer. FLAG.
[18] NOT_FOUND — needs a human answer. FLAG.
[19] NOT_FOUND — needs a human answer. FLAG.
[20] NOT_FOUND — needs a human answer. FLAG.
[21] NOT_FOUND — needs a human answer. FLAG.
[22] Low-confidence draft — evidence covers KMS not HSM; hedge is correct; needs security-team confirmation before send. EDIT to tighten. EDIT.
[23] Approved — region separation cited verbatim. APPROVE.
[24] NOT_FOUND — needs a human answer. FLAG.
[25] NOT_FOUND — needs a human answer. FLAG.
Outcome: 4 approved, 1 edited, 19 flagged as needing a human (the NOT_FOUND set).
