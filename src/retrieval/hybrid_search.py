"""Hybrid retrieval: BM25 keyword search + Chroma vector search, merged by reciprocal rank fusion."""

import re
import sqlite3
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from src.store.vectorstore import VectorStore

RRF_K = 60  # standard reciprocal-rank-fusion constant
CANDIDATE_POOL = 20  # how many results each retriever contributes before fusion
# Tuning pass 2 (see fixtures/eval/TUNING_LOG.md): equal weighting let BM25 term
# mismatch bury chunks with excellent vector distance (rank 1, well under the
# confidence threshold) at combined rank 6-7, outside the top-5 the model receives.
# 2.0 was the smallest value giving both known cases solid margin rather than landing
# right at the top_k=5 cutoff edge; verified empirically against the full eval set,
# not chosen analytically.
VECTOR_WEIGHT = 2.0


@dataclass
class RetrievedChunk:
    embedding_id: str
    source_filename: str
    heading_path: str
    loc_ref: str
    text: str
    vector_distance: float | None
    combined_score: float


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class HybridSearcher:
    def __init__(self, conn: sqlite3.Connection, vector_store: VectorStore):
        self._vector_store = vector_store
        rows = conn.execute("SELECT source_filename, heading_path, loc_ref, text, embedding_id FROM chunks").fetchall()
        self._chunks_by_id = {row["embedding_id"]: dict(row) for row in rows}
        self._ids = list(self._chunks_by_id.keys())
        self._bm25 = BM25Okapi([_tokenize(self._chunks_by_id[cid]["text"]) for cid in self._ids]) if self._ids else None

    def search(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        if not self._ids:
            return []

        bm25_scores = self._bm25.get_scores(_tokenize(query))
        # Filter zero-score BM25 candidates out BEFORE fusion. A chunk with no query
        # term overlap at all scores exactly 0.0; taking it into the candidate pool
        # gave it real reciprocal-rank credit purely from its sort position (BM25
        # sorts every chunk, zeros included, so with a pool larger than the number of
        # positive-scoring chunks, zero-overlap chunks filled the tail and earned
        # 1/(RRF_K + rank + 1) for nothing), which pushed genuine vector hits down the
        # fused list. That is precisely the failure the VECTOR_WEIGHT = 2.0 comment
        # below reweights around — filtering removes the symptom at its source: a
        # zero-overlap chunk still gets its vector credit, but no longer a BM25 credit
        # for a match it didn't make.
        scored = [(score, cid) for score, cid in zip(bm25_scores, self._ids) if score > 0]
        scored.sort(key=lambda pair: -pair[0])
        bm25_ranked = [cid for _, cid in scored[:CANDIDATE_POOL]]

        vector_hits = self._vector_store.query(query, top_k=CANDIDATE_POOL)
        vector_ranked = [hit["id"] for hit in vector_hits]
        distance_by_id = {hit["id"]: hit["distance"] for hit in vector_hits}

        rrf_scores: dict[str, float] = {}
        for rank, cid in enumerate(bm25_ranked):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, cid in enumerate(vector_ranked):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + VECTOR_WEIGHT * (1.0 / (RRF_K + rank + 1))

        top_ids = sorted(rrf_scores.keys(), key=lambda cid: -rrf_scores[cid])[:top_k]

        results = []
        for cid in top_ids:
            chunk = self._chunks_by_id[cid]
            results.append(
                RetrievedChunk(
                    embedding_id=cid,
                    source_filename=chunk["source_filename"],
                    heading_path=chunk["heading_path"],
                    loc_ref=chunk["loc_ref"],
                    text=chunk["text"],
                    vector_distance=distance_by_id.get(cid),
                    combined_score=rrf_scores[cid],
                )
            )
        return results
