"""Streamlit Cloud entrypoint: the review screen, read-only, over the committed demo store.

Pack 3, C2: a hosted review screen is the strongest conversion artifact this repo
has — a prospect clicking through verbatim cited evidence, a flagged-low row, and a
NOT FOUND IN PROVIDED DOCUMENTS row understands the product in about forty seconds.
This file is what Streamlit Community Cloud runs (the platform's default app
entrypoint); it points the review UI at the committed demo_store/ and forces
read-only mode, so the deployed app can never write to the store.

Deploy (one-time, needs a Streamlit account):
1. Push this repo to GitHub (it already is: PatricR73/Questionnaire-Responder).
2. On share.streamlit.io / the Streamlit Community Cloud dashboard, create an app
   from the repo, branch main, file streamlit_app.py.
3. No secrets are needed — everything shown is synthetic fixture data.
4. Post-deploy: set the app URL as the repo homepage (About panel) and swap the
   placeholder link in README.md's "Try it in 30 seconds" section.

Nothing here is real customer material: the store is built from fixtures/evidence/
by scripts/build_demo_store.py (see its module docstring for the curation step).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ["QRESP_DATA_DIR"] = str(ROOT / "demo_store")
os.environ["QRESP_REVIEW_READ_ONLY"] = "1"

from src.review_ui import main  # noqa: E402

main()
