---
name: Bug report
about: Something is wrong — include the run's config fingerprint so it can be reproduced.
title: ""
labels: ["bug"]
assignees: []
---

**Config fingerprint.** Every `answer` run prints a one-line `Config:` fingerprint at
start (model, thresholds, fusion constants, chunk bounds, embedding model, git
revision). Paste it here — without it, a bug report against a tuned run is not
reproducible.

```
Config: <paste the line printed at the start of the run>
```

**What did you run?**

- Command (or the CLI flags / config file / `QRESP_*` env vars you used)
- The questionnaire (fixture or shape — never attach a real customer workbook)
- `--provider stub` repro, if the bug reproduces without the API

**What happened?**

**What did you expect?**

**Relevant artifacts**

- The sidecar `.jsonl` and `.log.jsonl` next to the output workbook (the structured
  log carries per-row retrieval scores, token counts, and the full traceback for
  failed rows — it exists precisely so a bad row doesn't need to be re-run and hoped
  to reproduce)
- Anything from the review UI or the `run_config` column for the run

**Environment**

- Python version, install path (`requirements.lock` or `requirements.txt`)
- OS
