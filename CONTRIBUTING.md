# Contributing

Thanks for considering a contribution. This repo's central claim is that every
change is measured against a known baseline, so the bar for a change is: it ships
with a test, it passes the gates, and if it touches the eval it records the
before/after in `fixtures/eval/TUNING_LOG.md`.

## Dev setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"     # runtime deps + pinned pytest/ruff/mypy
```

`requirements.lock` is the pinned reproducibility path for the published eval
numbers; `requirements.txt` is the loose declaration. Prefer the lockfile for
reproducing results, and recompile it (`pip-compile requirements.txt -o
requirements.lock --no-annotate`) when you change runtime dependencies.

## Running tests

```
pytest tests/
```

No API key is needed — the suite uses `--provider stub` and fake clients. The
one-time embedding-model download (cached by CI) is the only network access.

## Running the eval

```
pip install -r requirements.lock
python fixtures/eval/run_eval.py --repeats 3
```

Requires `ANTHROPIC_API_KEY` and spend. If your change affects retrieval,
generation, or confidence, the eval before/after goes in `TUNING_LOG.md` in the
established entry format — including when the number drops or when the change
doesn't help.

## Gates

Before pushing, all three must pass:

```
pytest tests/
ruff check src/ tests/ fixtures/
ruff format --check src/ tests/ fixtures/
mypy src/
```

## Commit conventions

One commit per coherent change; the subject explains WHY, the body explains the
non-obvious decisions. See [`CLAUDE.md`](CLAUDE.md) for the full conventions —
including the rule that any change to published numbers requires a sweep of
"above"/"below"/"see the table" cross-references across the docs, and that
published numbers come from a real run, not aspiration.
