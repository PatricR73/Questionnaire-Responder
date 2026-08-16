"""CLI entrypoint: `ingest` an evidence directory, then `answer` a questionnaire against it.

Usage:
    python -m src.pipeline ingest --evidence-dir fixtures/evidence/
    python -m src.pipeline answer --questionnaire fixtures/questionnaire_sample.xlsx --output out/filled.xlsx --limit 5
    python -m src.pipeline answer --questionnaire fixtures/questionnaire_sample.xlsx --output out/filled.xlsx --provider stub

`answer` always saves the workbook on exit — including on an unhandled exception — via
a try/finally, and additionally saves every SAVE_EVERY_N_ROWS rows so progress is
visible on disk during a long run. The finally save is what guarantees correctness
(the xlsx always reflects everything processed so far, matching the sidecar .jsonl
log); the periodic save is purely a "don't wait until the end to see progress"
convenience on top of that guarantee, not a substitute for it. (The one thing neither
can help with is a hard kill, e.g. SIGKILL — no process can run cleanup code after
that; only a caught exception or a normal/Ctrl-C exit triggers the finally save.)

Answering is delegated to an Answerer (src/answer/answerer.py) selected via --provider:
"anthropic" (default, real Claude calls) or "stub" (no network/model — for exercising
the pipeline's plumbing and failure paths for free). A missing API key with
--provider anthropic always errors; there is no automatic fallback to stub. Stub runs
are stamped into the audit log and get a visible banner row in the output workbook so
a stub file can never be mistaken for a real one.

A per-row exception (from either Answerer) is caught here — not inside the Answerer —
and recorded as AnswerStatus.ERROR, written to the cell as a distinct state (never
conflated with a verified "no evidence found"; see write_xlsx.ERROR_MARKER), and the
loop continues to the next row.
"""

import json
import os
import time
from pathlib import Path

import anthropic
import click
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from src.answer.answerer import AnswerStatus, AnthropicAnswerer, StubAnswerer
from src.answer.split_questions import split_question
from src.ingest.embed import ingest_evidence
from src.questionnaire.parse_xlsx import detect_columns, read_questions
from src.questionnaire.write_xlsx import write_answer
from src.retrieval.hybrid_search import HybridSearcher
from src.store import db
from src.store.vectorstore import VectorStore

SAVE_EVERY_N_ROWS = 5

# Errors that will fail EVERY row identically and are never worth a per-row retry
# ladder: a 401 (AuthenticationError), a permission problem, a wrong model name
# (NotFoundError), or a schema-rejecting 400 (BadRequestError). Caught per-row
# today, each of these burns the full retry budget on every one of 400 rows and
# produces a workbook of red ERROR cells instead of stopping in the first ten
# seconds. They propagate out of the per-row handler and abort the run — the
# finally save still writes everything already processed, so no paid-for work is
# lost. Anything not in this set is treated as transient per-row noise.
FATAL_ERRORS = (
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
    anthropic.BadRequestError,
)

# Circuit breaker: N consecutive per-row errors (of any kind) abort the run. A
# systemic failure that isn't in FATAL_ERRORS — e.g. a network path that breaks for
# everyone, or an environment issue — would otherwise burn the whole sheet one row
# at a time; a healthy run never sees anywhere near this many in a row.
CONSECUTIVE_ERROR_LIMIT = 5

STUB_BANNER_TEXT = "⚠ STUB PROVIDER — THESE ARE NOT REAL ANSWERS (TESTING ONLY)"
STUB_BANNER_FILL = PatternFill(start_color="D32F2F", end_color="D32F2F", fill_type="solid")
STUB_BANNER_FONT = Font(bold=True, color="FFFFFF")


@click.group()
def cli():
    pass


@cli.command()
@click.option("--evidence-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
def ingest(evidence_dir: Path):
    click.echo(f"Ingesting evidence from {evidence_dir} ...")
    click.echo("(first run downloads the local embedding model — this can take a few minutes and looks like a hang)")
    conn = db.connect()
    vector_store = VectorStore()
    n = ingest_evidence(evidence_dir, conn, vector_store)
    click.echo(f"Ingested {n} chunks.")


def _add_stub_banner(ws, last_col: int) -> None:
    banner_row = ws.max_row + 1
    ws.merge_cells(start_row=banner_row, start_column=1, end_row=banner_row, end_column=last_col)
    cell = ws.cell(row=banner_row, column=1, value=STUB_BANNER_TEXT)
    cell.fill = STUB_BANNER_FILL
    cell.font = STUB_BANNER_FONT
    cell.alignment = Alignment(horizontal="center")


@cli.command()
@click.option("--questionnaire", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--limit", type=int, default=5, show_default=True, help="Max question rows to process this run. Use 0 for no limit (process every detected question row).")
@click.option("--only-row", type=int, default=None, help="Process only this single sheet row (overrides --limit); useful for targeted checks.")
@click.option("--provider", type=click.Choice(["anthropic", "stub"]), default="anthropic", show_default=True)
@click.option("--stub-fail-row", type=int, default=None, help="With --provider stub, make that row raise, to exercise per-row error isolation.")
def answer(questionnaire: Path, output: Path, limit: int, only_row: int | None, provider: str, stub_fail_row: int | None):
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise click.ClickException("ANTHROPIC_API_KEY not set — export it before running with --provider anthropic (every row would fail identically). Use --provider stub if you want to test without a key.")
        answerer = AnthropicAnswerer()
    else:
        answerer = StubAnswerer(fail_row=stub_fail_row)

    workbook = openpyxl.load_workbook(questionnaire)
    ws = workbook.active

    column_map = detect_columns(ws)
    all_questions = read_questions(ws, column_map)
    if only_row is not None:
        questions = [q for q in all_questions if q.row_index == only_row]
        if not questions:
            raise click.ClickException(f"Row {only_row} is not a detected question row.")
    else:
        questions = all_questions if limit == 0 else all_questions[:limit]
    click.echo(f"Answering {len(questions)} row(s) with provider={provider}.")

    if provider == "stub":
        _add_stub_banner(ws, last_col=max(column_map.question_col, column_map.answer_col, column_map.vocab_col or 0))

    conn = db.connect()
    vector_store = VectorStore()
    searcher = HybridSearcher(conn, vector_store)
    run_id = db.start_questionnaire_run(conn, str(questionnaire), str(output))

    output.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path = output.with_suffix(".jsonl")

    counts = {"high": 0, "low": 0, "none": 0, "error": 0}
    total_input_tokens = 0
    total_output_tokens = 0
    consecutive_errors = 0

    try:
        with open(jsonl_path, "a") as jsonl_file:
            for i, q in enumerate(questions, start=1):
                start = time.monotonic()
                try:
                    sub_question = split_question(q.question_text)[0]
                    evidence = searcher.search(sub_question, top_k=5)
                    result = answerer.answer_question(sub_question, evidence, column_map.vocab_values, row_index=q.row_index)

                    if result.status == AnswerStatus.NOT_FOUND:
                        final_confidence = "none"
                    elif result.status == AnswerStatus.ANSWERED:
                        final_confidence = result.confidence  # "high" or "low"
                    else:
                        final_confidence = "error"  # Answerers never return ERROR themselves; kept for completeness

                    answer_text = result.answer
                    vocab_selection = result.vocab_selection
                    self_confidence = result.confidence
                    polarity = result.polarity
                    cited_chunk_ids = result.cited_chunk_ids
                    sources = [f"{c.source_filename} ({c.heading_path or 'no heading'}, {c.loc_ref})" for c in evidence]
                    error_message = None
                    total_input_tokens += result.input_tokens
                    total_output_tokens += result.output_tokens
                except FATAL_ERRORS as exc:
                    # Wrong key, wrong model, or a schema-rejecting request: the same
                    # failure for every remaining row. Abort now (finally saves
                    # everything processed so far) instead of failing 400 rows one at
                    # a time after a full retry ladder each.
                    click.echo(f"  row {q.row_index}: FATAL — {type(exc).__name__}: {exc}")
                    raise click.ClickException(
                        f"Run aborted on row {q.row_index}: {type(exc).__name__} — this error will "
                        f"repeat for every row (check the API key, model name, and request schema). "
                        f"Everything processed so far is saved to {output}."
                    ) from exc
                except Exception as exc:  # noqa: BLE001 — per-row isolation is the point
                    consecutive_errors += 1
                    if consecutive_errors >= CONSECUTIVE_ERROR_LIMIT:
                        click.echo(f"  row {q.row_index}: ERROR — {exc}")
                        raise click.ClickException(
                            f"Run aborted after {CONSECUTIVE_ERROR_LIMIT} consecutive per-row errors "
                            f"(circuit breaker) — the failure is systemic, not per-row. Last error: {exc}. "
                            f"Everything processed so far is saved to {output}."
                        ) from exc
                    final_confidence = "error"
                    cited_chunk_ids = []
                    sources = []
                    answer_text = None
                    vocab_selection = None
                    self_confidence = None
                    polarity = None
                    sub_question = q.question_text
                    error_message = str(exc)
                    click.echo(f"  row {q.row_index}: ERROR — {error_message}")
                else:
                    consecutive_errors = 0

                elapsed = time.monotonic() - start

                write_answer(ws, q.row_index, column_map, answer_text or "", vocab_selection, final_confidence)
                db.record_answer(
                    conn, run_id, q.row_index, q.question_text, sub_question,
                    answer_text, vocab_selection, self_confidence, final_confidence, cited_chunk_ids,
                    polarity=polarity,
                )
                db.record_audit_entry(conn, run_id, q.row_index, sources, final_confidence, provider=provider)

                jsonl_file.write(json.dumps({
                    "row_index": q.row_index,
                    "question_text": q.question_text,
                    "final_confidence": final_confidence,
                    "polarity": polarity,
                    "provider": provider,
                    "answer": answer_text,
                    "error": error_message,
                    "elapsed_seconds": round(elapsed, 2),
                }) + "\n")
                jsonl_file.flush()

                counts[final_confidence] += 1
                if error_message is None:
                    click.echo(f"  row {q.row_index}: {final_confidence} ({elapsed:.1f}s)")

                if i % SAVE_EVERY_N_ROWS == 0:
                    workbook.save(output)
    finally:
        # Guarantees the xlsx always reflects everything processed so far, even on an
        # unhandled exception or Ctrl-C — the periodic save above is just a progress
        # convenience, not what correctness depends on.
        workbook.save(output)

    n_answered = counts["high"] + counts["low"]
    click.echo(
        f"Wrote {output} (+ {jsonl_path.name}). "
        f"answered={n_answered} flagged_low={counts['low']} not_found={counts['none']} error={counts['error']}"
    )
    if provider == "anthropic" and n_answered:
        click.echo(
            f"Tokens: {total_input_tokens} in / {total_output_tokens} out "
            f"(avg {total_input_tokens // n_answered} in / {total_output_tokens // n_answered} out per question)"
        )


if __name__ == "__main__":
    cli()
