"""End-to-end smoke test: no API key required.

Runs ingest -> questionnaire parsing -> hybrid retrieval against the real fixtures,
then asserts the pipeline stops at the Claude call with a clear error. This is a
regression guard for parsing/chunking/retrieval that costs nothing to run in CI: if
any of those stages breaks, this test fails before it ever reaches the API boundary.
"""

from pathlib import Path

import openpyxl
import pytest

from src.answer.generate import generate_answer
from src.ingest.embed import ingest_evidence
from src.questionnaire.parse_xlsx import detect_columns, read_questions
from src.retrieval.hybrid_search import HybridSearcher
from src.store import db
from src.store.vectorstore import VectorStore

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_pipeline_reaches_llm_call_and_stops_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    conn = db.connect(tmp_path / "store.db")
    vector_store = VectorStore(persist_dir=tmp_path / "chroma")

    n_chunks = ingest_evidence(FIXTURES / "evidence", conn, vector_store)
    assert n_chunks > 0

    workbook = openpyxl.load_workbook(FIXTURES / "questionnaire_sample.xlsx")
    ws = workbook.active
    column_map = detect_columns(ws)
    questions = read_questions(ws, column_map)
    assert len(questions) > 0

    searcher = HybridSearcher(conn, vector_store)
    evidence = searcher.search(questions[0].question_text, top_k=5)
    assert len(evidence) > 0

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        generate_answer(questions[0].question_text, evidence, column_map.vocab_values)
