"""CLI entrypoint: `ingest` an evidence directory, then `answer` a questionnaire against it.

Usage:
    python -m src.pipeline ingest --evidence-dir fixtures/evidence/
    python -m src.pipeline answer --questionnaire fixtures/questionnaire_sample.xlsx --output out/filled.xlsx --limit 5
    python -m src.pipeline answer --questionnaire fixtures/questionnaire_sample.xlsx --output out/filled.xlsx --provider stub

`answer` saves the workbook incrementally (every SAVE_EVERY_N_ROWS rows, plus always at
the end) and appends one JSON line per processed row to a sidecar .jsonl file next to
the output. A crash partway through a run therefore loses at most a few rows' worth of
paid API calls, not the whole run.

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
                cited_chunk_ids = result.cited_chunk_ids
                sources = [f"{c.source_filename} ({c.heading_path or 'no heading'}, {c.loc_ref})" for c in evidence]
                error_message = None
                total_input_tokens += result.input_tokens
                total_output_tokens += result.output_tokens
            except Exception as exc:  # noqa: BLE001 — per-row isolation is the point
                final_confidence = "error"
                cited_chunk_ids = []
                sources = []
                answer_text = None
                vocab_selection = None
                self_confidence = None
                sub_question = q.question_text
                error_message = str(exc)
                click.echo(f"  row {q.row_index}: ERROR — {error_message}")

            elapsed = time.monotonic() - start

            write_answer(ws, q.row_index, column_map, answer_text or "", vocab_selection, final_confidence)
            db.record_answer(
                conn, run_id, q.row_index, q.question_text, sub_question,
                answer_text, vocab_selection, self_confidence, final_confidence, cited_chunk_ids,
            )
            db.record_audit_entry(conn, run_id, q.row_index, sources, final_confidence, provider=provider)

            jsonl_file.write(json.dumps({
                "row_index": q.row_index,
                "question_text": q.question_text,
                "final_confidence": final_confidence,
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
