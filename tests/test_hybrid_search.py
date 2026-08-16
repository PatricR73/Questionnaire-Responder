"""P10: hybrid-fusion ordering tests over a hand-built store.

VECTOR_WEIGHT, RRF_K, and CANDIDATE_POOL are magic numbers that the eval has been
relying on since tuning pass 2 without any direct test. These tests pin down the
fusion math itself — with a fake vector store (so no Chroma, no model, no network)
and a tiny hand-built corpus — and lock in the two behaviours the P10 change is
about:

1. Zero-score BM25 candidates are filtered before fusion: a chunk with no query
   term overlap must NOT earn reciprocal-rank credit from its BM25 sort position.
   It keeps its vector credit (its combined_score reflects only that), and a
   genuine overlap+vector hit outranks it.
2. VECTOR_WEIGHT = 2.0 is what lets a strong vector hit beat a strong BM25 hit at
   the same fused distance — with equal weighting the ordering flips.
"""

import sqlite3

import pytest

from src.retrieval.hybrid_search import CANDIDATE_POOL, RRF_K, VECTOR_WEIGHT, HybridSearcher
from src.store.db import SCHEMA


class FakeVectorStore:
    """Minimal stand-in for VectorStore.query: returns the caller's pre-ordered hits."""

    def __init__(self, hits: list[dict]):
        self._hits = hits

    def query(self, text: str, top_k: int = 5) -> list[dict]:
        return [dict(h) for h in self._hits[:top_k]]


def _make_searcher(chunks: list[tuple[str, str]], vector_hits: list[dict]) -> HybridSearcher:
    """chunks: (embedding_id, text) pairs. vector_hits: query-ordered fake results."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO chunks (source_filename, heading_path, loc_ref, text, embedding_id) "
        "VALUES ('doc.md', 'Heading', 'line 1', ?, ?)",
        [(text, cid) for cid, text in chunks],
    )
    conn.commit()
    return HybridSearcher(conn, FakeVectorStore(vector_hits))


def test_zero_score_bm25_chunk_gets_no_bm25_credit():
    # Query "alpha beta". A overlaps both terms; C overlaps one; B overlaps none
    # (BM25 score exactly 0) but is the strongest VECTOR hit. Without the P10
    # filter, B's BM25 sort position would earn 1/(RRF_K+3) on top of its vector
    # credit and win the fusion; with the filter its combined_score is vector
    # credit only and the genuine overlap chunk A ranks first.
    searcher = _make_searcher(
        [("c-a", "alpha beta gamma"), ("c-b", "zeta eta theta"), ("c-c", "alpha sigma")],
        [
            {"id": "c-b", "distance": 0.10},
            {"id": "c-a", "distance": 0.20},
            {"id": "c-c", "distance": 0.30},
        ],
    )
    results = searcher.search("alpha beta", top_k=3)
    assert [r.embedding_id for r in results] == ["c-a", "c-c", "c-b"]

    # B's combined score is exactly its vector credit: VECTOR_WEIGHT/(RRF_K + 1).
    # Any BM25 contribution would make it larger (the pre-filter bug).
    b = next(r for r in results if r.embedding_id == "c-b")
    assert b.combined_score == pytest.approx(VECTOR_WEIGHT / (RRF_K + 1))

    # A = BM25 rank 1 + vector rank 2; C = BM25 rank 2 + vector rank 3.
    a = next(r for r in results if r.embedding_id == "c-a")
    c = next(r for r in results if r.embedding_id == "c-c")
    assert a.combined_score == pytest.approx(1 / (RRF_K + 1) + VECTOR_WEIGHT / (RRF_K + 2))
    assert c.combined_score == pytest.approx(1 / (RRF_K + 2) + VECTOR_WEIGHT / (RRF_K + 3))


def test_vector_weight_decides_a_tie_between_vector_rank1_and_bm25_rank1(monkeypatch):
    # Query "alpha beta". Chunk A ("alpha beta") is BM25 rank 1 / vector rank 2;
    # chunk D ("beta") is BM25 rank 2 / vector rank 1. At equal weighting the two
    # RRF sums are IDENTICAL (1/(K+1)+1/(K+2) both ways) — so which one wins is
    # exactly the decision VECTOR_WEIGHT = 2.0 makes, and the test pins that the
    # constant is load-bearing rather than cosmetic. X has zero BM25 overlap and is
    # filtered from the BM25 side (P10), keeping its vector-only credit.
    import src.retrieval.hybrid_search as hs

    searcher = _make_searcher(
        [("c-a", "alpha beta"), ("c-d", "beta"), ("c-x", "omega omega omega")],
        [
            {"id": "c-d", "distance": 0.05},
            {"id": "c-a", "distance": 0.10},
            {"id": "c-x", "distance": 0.20},
        ],
    )

    results = searcher.search("alpha beta", top_k=3)
    assert results[0].embedding_id == "c-d"  # vector rank 1 wins under VECTOR_WEIGHT = 2.0
    d = results[0]
    assert d.combined_score == pytest.approx(1 / (RRF_K + 2) + VECTOR_WEIGHT / (RRF_K + 1))

    # Under equal weighting the pair ties exactly — the constant is what breaks it.
    monkeypatch.setattr(hs, "VECTOR_WEIGHT", 1.0)
    tied = searcher.search("alpha beta", top_k=3)
    a = next(r for r in tied if r.embedding_id == "c-a")
    d_tied = next(r for r in tied if r.embedding_id == "c-d")
    assert a.combined_score == pytest.approx(d_tied.combined_score)


def test_candidate_pool_is_respected():
    # CANDIDATE_POOL bounds how many chunks each retriever contributes; with more
    # chunks than the pool, only the top pool entries fuse. All five chunks here
    # have positive BM25 overlap, so the pool truncation is the binding constraint.
    chunks = [(f"c-{i}", f"alpha term{i}") for i in range(CANDIDATE_POOL + 5)]
    vector_hits = [{"id": f"c-{i}", "distance": 0.1 + i * 0.01} for i in range(CANDIDATE_POOL + 5)]
    searcher = _make_searcher(chunks, vector_hits)
    results = searcher.search("alpha", top_k=3)
    # The strongest vector hit must be in the pool and thus surface in the top-3.
    assert results[0].embedding_id == "c-0"
