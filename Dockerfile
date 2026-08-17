# Vendor Security Questionnaire Responder — one-command demo image (pack 3, C1).
#
# From a fresh clone, one command gets a prospect from nothing to a filled workbook
# plus a running review screen, with no local Python setup, no API key, and — because
# the embedding model is baked into this image at build time — no model download:
#
#     docker build -t qresp-demo .
#     docker run -p 8501:8501 qresp-demo
#
# The image installs the exact pinned environment (requirements.lock) and pre-builds
# the local embedding model, so the container is intentionally large: build once, and
# every later "docker run" is instant and fully offline. Only the review screen and
# local retrieval ever run; --provider stub means no Anthropic call is made at all.
# See README.md "Try it in 30 seconds" for the narrative and docs/DESIGN.md for what
# a real run adds (an API key and --provider anthropic).

FROM python:3.14-slim

# Non-root runtime user; the demo writes its store and workbook under /app/out.
RUN useradd --create-home --uid 10001 responder

WORKDIR /app

# Dependencies first (layer-cached across rebuilds unless the lock changes).
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

# The package itself (installs the `qresp` console command) and the pre-built demo
# store (SQLite + Chroma index built from the synthetic fixtures/evidence/), so the
# demo needs no ingest step.
COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir -e .
COPY demo_store/ ./demo_store/
COPY fixtures/ ./fixtures/

# The demo's run data and outputs.
RUN mkdir -p /app/out && chown -R responder:responder /app/out

# Bake the local embedding model into the image so "docker run" performs no
# downloads — this is what makes the one-liner truly zero-setup. Pinned to the same
# revision EVAL.md's numbers were measured against (see src/store/vectorstore.py).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5', revision='5c38ec7c405ec4b44b94cc5a9bb96e735b38267a')"

USER responder

EXPOSE 8501

# The one-command demo: fill the committed eval questionnaire with --provider stub,
# print the output paths, then serve the review screen on :8501.
CMD ["qresp", "demo"]
