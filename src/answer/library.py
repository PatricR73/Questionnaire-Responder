"""The answer library: human-approved answers surfaced as candidates for new questions.

Pack 3, C4. Commercially this is the feature customers renew for — the first
questionnaire saves some time; the second one, drawing on approved answers from the
first, is where a buyer sees the tool pay for itself. The design constraint that
keeps it safe is deliberately narrow:

- The library is a SEPARATE namespace (reviewed_answers in SQLite). HybridSearcher
  reads only the chunks table, so an approved answer is structurally impossible to
  retrieve as document evidence — it is a candidate for the generator, never a
  citation source.
- Citation grounding and the optional entailment check run against the ORIGINAL
  evidence, never against the prior answer. A previously approved answer is a
  strong prior, not a source of truth: if the underlying policy changed, the
  library must not launder a stale claim.
- Freshness is enforced at retrieval time: entries whose source documents have
  changed (content hashes recorded at ingest) are excluded by
  db.find_reviewed_answers; semantic matching below applies the same gate.

Matching: exact normalized text first (most questions repeat verbatim across
questionnaires), then semantic similarity of the question text (embedded with the
same local model the query path already loads). The candidate is presented to the
generator with its full provenance — original question, source run, row, action,
timestamp — and the workbook row is marked when the answer drew on it.
"""

import sqlite3
from difflib import SequenceMatcher

from src.store import db
from src.store.vectorstore import VectorStore

# Cosine similarity above which a prior question counts as "semantically
# equivalent". 0.75 is deliberately conservative: false positives here surface a
# possibly-wrong prior answer into the prompt (the generator still has to ground it
# in current evidence, so the cost is low), but a flood of irrelevant candidates
# would train reviewers to ignore the marker. Revisit with measured data if the
# library eval (TUNING_LOG.md) shows misses on clearly-equivalent questions.
SEMANTIC_THRESHOLD = 0.75


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def find_candidates(
    conn: sqlite3.Connection,
    question_text: str,
    vector_store: VectorStore | None,
    *,
    threshold: float = SEMANTIC_THRESHOLD,
    limit: int = 3,
) -> list[dict]:
    """Prior approved answers for this question, freshness-gated, best first.

    Exact normalized matches always win (similarity 1.0). If no exact match and a
    vector store is available, the question is embedded and compared against the
    stored prior question texts; entries above threshold are returned with a
    similarity score. Every returned entry has passed db.find_reviewed_answers'
    staleness gate (source docs unchanged)."""
    exact = db.find_reviewed_answers(conn, question_text, limit=limit)
    if exact:
        for e in exact:
            e["similarity"] = 1.0
        return exact

    if vector_store is None:
        return []

    prior = conn.execute(
        "SELECT id, question_text, answer_text, polarity, source_doc_hashes, run_id, row_index, "
        "human_action, reviewed_at FROM reviewed_answers ORDER BY reviewed_at DESC LIMIT 500"
    ).fetchall()
    if not prior:
        return []

    current = db.current_source_hashes(conn)
    fresh = []
    for r in prior:
        hashes = __import__("json").loads(r["source_doc_hashes"]) if r["source_doc_hashes"] else {}
        if not hashes:
            continue
        if any(current.get(fname) != h for fname, h in hashes.items()):
            continue
        fresh.append(dict(r))
    if not fresh:
        return []

    # Batch-embed the stored question texts; the embedding model is already loaded
    # on the query path, so this costs one batched local encode per question.
    from src.retrieval.hybrid_search import _tokenize  # noqa: F401  (keeps import graph honest)

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if not na or not nb:
            return 0.0
        return dot / (na * nb)

    query_vec = vector_store.embed_query(question_text)
    if query_vec is None:
        return []
    stored = vector_store.embed_texts([r["question_text"] for r in fresh])
    scored = []
    for r, vec in zip(fresh, stored):
        sim = float(_cosine(query_vec, vec))
        if sim >= threshold:
            r["similarity"] = round(sim, 4)
            scored.append(r)
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:limit]


def format_prior_answer_block(candidate: dict) -> str:
    """The labelled candidate block appended to the generator's user message.

    Provenance is the point: the generator must know this is a human-approved prior
    answer to a (nearly) equivalent question, not current evidence — it is a
    candidate to consider and verify, and the citation/entailment checks downstream
    still run against the original evidence only."""
    provenance = (
        f"previously approved answer to a similar question "
        f"(run {candidate.get('run_id')}, row {candidate.get('row_index')}, "
        f"{candidate.get('human_action')} at {candidate.get('reviewed_at')}, "
        f"similarity {candidate.get('similarity', '?')})"
    )
    return f"[PRIOR APPROVED ANSWER — {provenance}]\n{candidate['answer_text']}\n[END PRIOR APPROVED ANSWER]"


def answer_uses_prior(final_answer: str, candidate: dict, threshold: float = 0.5) -> bool:
    """Whether the generated answer materially draws on the prior answer.

    SequenceMatcher ratio on the full texts; 0.5 is a low bar on purpose — the
    workbook marker should lean toward flagging (a reviewer checking a marked row
    is cheap; an unmarked row that quietly reused a prior answer is not)."""
    if not final_answer.strip() or not candidate.get("answer_text"):
        return False
    return SequenceMatcher(None, final_answer, candidate["answer_text"]).ratio() >= threshold
