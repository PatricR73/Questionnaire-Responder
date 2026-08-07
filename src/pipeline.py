"""CLI entrypoint: `ingest` an evidence directory, then `answer` a questionnaire against it.

Usage:
    python -m src.pipeline ingest --evidence-dir fixtures/evidence/
    python -m src.pipeline answer --questionnaire fixtures/questionnaire_sample.xlsx --output out/filled.xlsx --limit 5

`answer` saves the workbook incrementally (every SAVE_EVERY_N_ROWS rows, plus always at
the end) and appends one JSON line per processed row to a sidecar .jsonl file next to
the output. A crash partway through a run therefore loses at most a few rows' worth of
paid API calls, not the whole run, and the run can be inspected/resumed from the JSONL
without re-reading the xlsx. A per-row failure (rate limit, transient API error,
malformed response) is caught, written to the cell as a distinct "error" state (never
conflated with "no evidence found" — see write_xlsx.ERROR_MARKER), and the loop
continues; only a missing API key aborts the whole run up front, since every row would
fail identically.
"""

import json
import os
import time
from pathlib import Path

import click
import openpyxl

from src.answer.confidence import cross_check_confidence
from src.answer.generate import generate_answer
from src.answer.split_questions import split_question
from src.ingest.embed import ingest_evidence
from src.questionnaire.parse_xlsx import detect_columns, read_questions
from src.questionnaire.write_xlsx import write_answer
from src.retrieval.hybrid_search import HybridSearcher
from src.store import db
from src.store.vectorstore import VectorStore

SAVE_EVERY_N_ROWS = 5


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


@cli.command()
@click.option("--questionnaire", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.option("--limit", type=int, default=5, show_default=True, help="Max question rows to send to Claude this run.")
@click.option("--only-row", type=int, default=None, help="Process only this single sheet row (overrides --limit); useful for targeted checks.")
def answer(questionnaire: Path, output: Path, limit: int, only_row: int | None):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise click.ClickException("ANTHROPIC_API_KEY not set — export it before running `answer` (every row would fail identically).")

    workbook = openpyxl.load_workbook(questionnaire)
    ws = workbook.active

    column_map = detect_columns(ws)
    all_questions = read_questions(ws, column_map)
    if only_row is not None:
        questions = [q for q in all_questions if q.row_index == only_row]
        if not questions:
            raise click.ClickException(f"Row {only_row} is not a detected question row.")
    else:
        questions = all_questions[:limit]
    click.echo(f"Answering {len(questions)} row(s).")

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
                draft = generate_answer(sub_question, evidence, column_map.vocab_values)
                final_confidence = cross_check_confidence(draft, evidence)
                cited_chunk_ids = [c.embedding_id for c in evidence]
                sources = [f"{c.source_filename} ({c.heading_path or 'no heading'}, {c.loc_ref})" for c in evidence]
                answer_text = draft.answer
                vocab_selection = draft.vocab_selection
                self_confidence = draft.self_confidence
                total_input_tokens += draft.input_tokens
                total_output_tokens += draft.output_tokens
                error_message = None
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
            db.record_audit_entry(conn, run_id, q.row_index, sources, final_confidence)

            jsonl_file.write(json.dumps({
                "row_index": q.row_index,
                "question_text": q.question_text,
                "final_confidence": final_confidence,
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
    n_calls = counts["high"] + counts["low"] + counts["none"]
    click.echo(f"Wrote {output} (+ {jsonl_path.name}). high={counts['high']} low={counts['low']} none={counts['none']} error={counts['error']}")
    if n_calls:
        click.echo(
            f"Tokens: {total_input_tokens} in / {total_output_tokens} out "
            f"(avg {total_input_tokens // n_calls} in / {total_output_tokens // n_calls} out per question)"
        )


if __name__ == "__main__":
    cli()
