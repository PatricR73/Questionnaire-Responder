# Sample output — stub provider

Everything in this folder is **stub-provider output**: the pipeline ran with
`--provider stub`, so no API call was made, no model was consulted, and the answers
are deterministic markers built from the retrieved evidence. The red banner row in
the workbook and `provider: "stub"` in the audit log are the stamps that make this
impossible to mistake for a real run. It is reproducible and free:

```
python -m src.pipeline answer --questionnaire fixtures/eval/questionnaire_eval.xlsx \
  --output docs/sample/filled_stub.xlsx --limit 0 --provider stub
```

The real deliverable — what a filled questionnaire looks like after a run and a
human review — is a workbook whose answer cells are either a drafted answer citing
the evidence, a neutral `NOT FOUND IN PROVIDED DOCUMENTS` fill, or the red error
marker. Everything else (merged cells, section headers, spacers, formatting) is
untouched.

| File | What it is |
|---|---|
| `filled_stub.xlsx` | The pipeline's output over the 24-question eval workbook |
| `filled_stub_reviewed.xlsx` | The reviewed export after approving/editing/rejecting rows in the review UI |
| `filled_stub.jsonl` | The per-row sidecar log (run id, timing, status) — the structured `.log.jsonl` debug log also exists next to the workbook but is gitignored by design |
| `review_ui_main.png` | The review screen showing the run's rows |
| `review_ui_filter_high.png` | The review screen with the confidence filter applied |

## Reading a filled workbook

The stub answers what retrieval can support (best match at or under the cosine
distance threshold) and abstains otherwise. Four row types, one of each:

**High-confidence answer — row 3, BCR-08.1 ("Is cloud data periodically backed
up?").** The stub's answer cell reads `[STUB] Per business_continuity_plan.docx —
Business Continuity Plan > Backup Strategy: Production databases are backed up
hourly with 30-day retention...`. In a real run this cell holds the model's drafted
answer plus a verbatim citation, written with no fill — the row a reviewer reads
fastest.

**Flagged-low answer — the stub never produces one, which is itself the honest
example.** The stub has no model to be uncertain, so every answer it makes is
"high". In a real run, low rows get a yellow fill and a "needs human review"
comment. The captured real run shows why: IAM-08.1 ("access reviews... least
privilege and separation of duties") came back `low/partial` —

> The evidence indicates that access to production systems is reviewed quarterly by
> the security team, and accounts unused for 90 days are automatically disabled.
> However, the evidence does not explicitly state that these reviews are conducted
> for the specific purposes of enforcing least privilege or separation of duties...

The evidence supports the review cadence but not the purposes the question asks
about — the model hedged correctly, the row was flagged, and a human decided.

**NOT FOUND — row 14, LOG-12.1.** The answer cell holds
`NOT FOUND IN PROVIDED DOCUMENTS` with the neutral grey fill and a comment saying
the row needs a human answer. This is the tool's core behavior: a question the
evidence doesn't answer is never guessed at.

**The known finding — ADV-04 ("access reviews automated through an IGA
platform?").** In the stub run this is a plain NOT FOUND row. In the real
adversarial run it exposed the finding recorded in TUNING_LOG.md pass 7: the model's
answer text abstains correctly —

> ...the evidence does not state that these reviews are conducted through, or
> automated by, an identity governance and administration (IGA) platform, so this
> specific claim cannot be confirmed.

— but the structured output reported `supported=true`, so the pipeline recorded
the row as `ANSWERED` (low/partial) instead of `NOT_FOUND`. That is a
status-mapping defect, not a fabricated assertion: the text itself is a correct
abstention. Showing it here is the point — the most credible thing this project
produces is an honest record of where it falls short.

## Review UI

`streamlit run src/review_ui.py` (bind to localhost: `--server.address=127.0.0.1`)
shows the run's rows with confidence badges, the drafted answer beside the cited
evidence, and Approve / Edit / Reject / Undo actions. The screenshots show the
sidebar run picker, the review progress, and the row list.
