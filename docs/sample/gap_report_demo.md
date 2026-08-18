# Documentation gap report — run 1

Source questionnaire: `/home/retrix/Desktop/Questionnaire-Responder/vendor-security-questionnaire-rag/fixtures/eval/questionnaire_eval.xlsx`

**19 of 24 questions are unanswerable from the current documentation** (plus 1 answered but flagged low-confidence). Each gap below shows the closest evidence retrieval actually found and its cosine distance, so 'nothing found' and 'found something adjacent but not on point' are distinguishable.

## Most affected domains

| Domain | Gaps |
|---|---|
| IAM | 4 |
| BCR | 3 |
| SEF | 3 |
| ADV | 2 |
| A&A | 1 |
| CEK | 1 |
| DSP | 1 |
| HRS | 1 |
| LOG | 1 |
| STA | 1 |
| UEM | 1 |

## Unanswerable questions (NOT FOUND)

| Row | Domain | Question | Closest evidence retrieved | Distance |
|---|---|---|---|---|
| 4 | IAM | Are reviews and revalidation of user access for least privilege and separation of duties completed w | access_control_policy.md / Access Control Policy > Access Reviews | adjacent (distance 0.318) |
| 5 | IAM | Are processes, procedures, and technical measures that ensure users are identifiable through unique  | it_operations_standards.md / IT Operations Standards > Authentication | adjacent (distance 0.404) |
| 6 | BCR | Are strategies developed to reduce the impact of, withstand, and recover from business disruptions i | business_continuity_plan.docx / Business Continuity Plan > Disaster Recovery | adjacent (distance 0.451) |
| 7 | SEF | Are processes, procedures, and technical measures supporting business processes to triage security-r | access_control_policy.md / Access Control Policy > Encryption > Encryption in transit | adjacent (distance 0.387) |
| 8 | UEM | Are processes, procedures, and technical measures defined, implemented, and evaluated to enable remo | access_control_policy.md / Access Control Policy > Encryption > Encryption at rest | adjacent (distance 0.381) |
| 10 | STA | Do service agreements between CSPs and CSCs (tenants) incorporate at least the following mutually ag | access_control_policy.md / Access Control Policy > Encryption > Encryption in transit | adjacent (distance 0.407) |
| 11 | BCR | Is the confidentiality, integrity, and availability of backup data ensured? | access_control_policy.md / Access Control Policy > Encryption > Encryption at rest | adjacent (distance 0.347) |
| 12 | CEK | Are cryptography, encryption, and key management policies and procedures established, documented, ap | access_control_policy.md / Access Control Policy > Encryption > Encryption at rest | adjacent (distance 0.326) |
| 13 | SEF | Are policies and procedures for timely management of security incidents established, documented, app | business_continuity_plan.docx / Incident Response > Escalation | adjacent (distance 0.438) |
| 14 | LOG | Is physical access logged and monitored using an auditable access control system? | access_control_policy.md / Access Control Policy > Access Reviews | adjacent (distance 0.363) |
| 15 | A&A | Are independent audit and assurance assessments conducted according to relevant standards at least a | access_control_policy.md / Access Control Policy > Access Reviews | adjacent (distance 0.448) |
| 16 | HRS | Are background verification policies and procedures of all new employees (including but not limited  | it_operations_standards.md / IT Operations Standards > Authentication | adjacent (distance 0.387) |
| 17 | DSP | Are processes, procedures, and technical measures defined, implemented, and evaluated to disclose de | it_operations_standards.md / IT Operations Standards > Software Provisioning | adjacent (distance 0.385) |
| 18 | BCR | Can backups be restored appropriately for resiliency? | business_continuity_plan.docx / Business Continuity Plan > Backup Strategy | adjacent (distance 0.347) |
| 19 | SEF | Are processes, procedures, and technical measures for security breach notifications defined and impl | access_control_policy.md / Access Control Policy > Encryption > Encryption in transit | adjacent (distance 0.396) |
| 20 | IAM | Are processes, procedures, and technical measures for the secure management of passwords defined, im | access_control_policy.md / Access Control Policy > Encryption > Encryption at rest | adjacent (distance 0.351) |
| 21 | IAM | Are processes, procedures, and technical measures for authenticating access to systems, application, | it_operations_standards.md / IT Operations Standards > Authentication | adjacent (distance 0.305) |
| 24 | ADV | Are passwords stored using a salted, one-way cryptographic hashing algorithm? | access_control_policy.md / Access Control Policy > Encryption > Encryption at rest | adjacent (distance 0.345) |
| 25 | ADV | Are user access reviews automated through an identity governance and administration (IGA) platform? | access_control_policy.md / Access Control Policy > Access Reviews | adjacent (distance 0.321) |

## Documented but weakly supported (low confidence)

| Row | Domain | Question | Polarity |
|---|---|---|---|
| 22 | ADV | Are encryption keys stored in a hardware security module (HSM)? | partial |

_Generated by `qresp gap-report` — retrieval-only, no API calls, no model beyond the local embedding._