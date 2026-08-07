# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## Commits

- One commit per coherent change. Never batch unrelated work into a single commit.
- Subject line explains WHY, imperative mood, under 72 characters. The diff already
  says what changed — the subject line's job is to say why it changed.
- Body explains the reasoning for any non-obvious decision (why this approach, what
  it replaces, what tradeoff was made) — not a restatement of the diff.
- Never commit without running `pytest tests/test_pipeline_smoke.py` first.
- Never use `git add -A`. Stage named paths.
- Never commit anything matching `.gitignore`, and never `git add -f`.
