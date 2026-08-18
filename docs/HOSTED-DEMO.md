# Hosting the read-only demo review screen

Pack 3, C2. The strongest conversion artifact this repo has is a review screen a
prospect can actually click through: verbatim cited evidence, a flagged-low row, and
a `NOT FOUND IN PROVIDED DOCUMENTS` row. Hosting it read-only over the committed
demo store is safe (nothing is writable) and cheap (Streamlit Community Cloud is
free), and nothing in the store resembles real customer material — everything comes
from the synthetic fixtures under `fixtures/evidence/`.

## What you get after this

A public URL where a prospect can pick the frozen sample run and review all 24
rows — five with curated, cited drafts (one flagged low, one a documented negative),
nineteen honest abstentions — with Approve/Edit/Reject hidden and a banner linking
back to the repo.

## Deploy (one-time, ~5 minutes)

1. Push this repo to GitHub. It already lives at
   `PatricR73/Questionnaire-Responder` (main).
2. Go to the [Streamlit Community Cloud](https://streamlit.io/cloud) dashboard and
   create a new app from that repo: branch `main`, main file `streamlit_app.py`.
3. No secrets, no env vars. The app sets `QRESP_DATA_DIR` to the committed
   `demo_store/` and `QRESP_REVIEW_READ_ONLY=1` itself (see
   `streamlit_app.py`).
4. Deploy. The URL looks like
   `https://questionnaire-responder-demo.streamlit.app`.

## Post-deploy checklist

- Set the app URL as the repository homepage (GitHub → repo → About → Website) so
  the About panel carries the live demo link. Until the deploy happens, the homepage is set to the
  case study (docs/CASE-STUDY.md) — swap it for the demo URL on deploy.
- Swap the placeholder "live demo" link in README.md's *Try it in 30 seconds*
  section for the real URL.
- Add the URL to the repo description if useful ("Try the live demo: <url>").

## How the demo store is built

```
python scripts/build_demo_store.py
```

Ingests `fixtures/evidence/`, runs the real CLI with `--provider stub` over the
committed 24-question eval workbook, then curates the five answered rows into
grounded drafts with a human review trail (see the script's module docstring for
exactly what is curated and why the store must never be pointed at by the eval
harness). The result is committed under `demo_store/` (≈0.5 MB).
