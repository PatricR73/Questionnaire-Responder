"""CLI entrypoint: `ingest` an evidence directory, then `answer` a questionnaire against it.

Usage:
    python -m src.pipeline ingest --evidence-dir fixtures/evidence/
    python -m src.pipeline answer --questionnaire fixtures/questionnaire_sample.xlsx --output out/filled.xlsx --limit 5
"""

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
def answer(questionnaire: Path, output: Path, limit: int):
    workbook = openpyxl.load_workbook(questionnaire)
    ws = workbook.active

    column_map = detect_columns(ws)
    questions = read_questions(ws, column_map)[:limit]
    click.echo(f"Answering {len(questions)} of the questionnaire's rows (--limit {limit}).")

    conn = db.connect()
    vector_store = VectorStore()
    searcher = HybridSearcher(conn, vector_store)
    run_id = db.start_questionnaire_run(conn, str(questionnaire), str(output))

    counts = {"high": 0, "low": 0, "none": 0}
    for q in questions:
        sub_question = split_question(q.question_text)[0]
        evidence = searcher.search(sub_question, top_k=5)
        draft = generate_answer(sub_question, evidence, column_map.vocab_values)
        final_confidence = cross_check_confidence(draft, evidence)

        write_answer(ws, q.row_index, column_map, draft.answer, draft.vocab_selection, final_confidence)

        cited_chunk_ids = [c.embedding_id for c in evidence]
        db.record_answer(
            conn,
            run_id,
            q.row_index,
            q.question_text,
            sub_question,
            draft.answer,
            draft.vocab_selection,
            draft.self_confidence,
            final_confidence,
            cited_chunk_ids,
        )
        sources = [f"{c.source_filename} ({c.heading_path or 'no heading'}, {c.loc_ref})" for c in evidence]
        db.record_audit_entry(conn, run_id, q.row_index, sources, final_confidence)

        counts[final_confidence] += 1
        click.echo(f"  row {q.row_index}: {final_confidence}")

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    click.echo(f"Wrote {output}. high={counts['high']} low={counts['low']} none={counts['none']}")


if __name__ == "__main__":
    cli()
