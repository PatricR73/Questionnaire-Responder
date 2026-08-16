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
    # P11: relevance score from the optional cross-encoder reranker (0-1,
    # sigmoid of the logit). None when the reranker is off. Carried ALONGSIDE
    # vector_distance, never overwriting it — the confidence layer may use it
    # later, but that decision is deliberately not wired in this commit.
    rerank_score: float | None = None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class HybridSearcher:
    def __init__(
        self,
        conn: sqlite3.Connection,
        vector_store: VectorStore,
        *,
        vector_weight: float = VECTOR_WEIGHT,
        rrf_k: int = RRF_K,
        candidate_pool: int = CANDIDATE_POOL,
        reranker=None,
    ):
        # Tuning knobs as instance attributes (defaulting to the module constants,
        # which keep their comments) so the P18 Config can thread resolved values
        # through without a source edit per tuning pass. reranker (a
        # CrossEncoderReranker, or None) is the P11 opt-in: when set, the fused
        # candidate pool is re-ranked by question-passage relevance before
        # truncation to top_k, and rerank_score rides on each RetrievedChunk.
        self._vector_weight = vector_weight
        self._rrf_k = rrf_k
        self._candidate_pool = candidate_pool
        self._reranker = reranker
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
        bm25_ranked = [cid for _, cid in scored[: self._candidate_pool]]

        vector_hits = self._vector_store.query(query, top_k=self._candidate_pool)
        vector_ranked = [hit["id"] for hit in vector_hits]
        distance_by_id = {hit["id"]: hit["distance"] for hit in vector_hits}

        rrf_scores: dict[str, float] = {}
        for rank, cid in enumerate(bm25_ranked):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (self._rrf_k + rank + 1)
        for rank, cid in enumerate(vector_ranked):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + self._vector_weight * (1.0 / (self._rrf_k + rank + 1))

        fused_ids = sorted(rrf_scores.keys(), key=lambda cid: -rrf_scores[cid])
        if self._reranker is not None:
            # P11: cross-encoder rerank the whole fused pool (both retrievers'
            # candidates, before truncation) — the near-miss distractor problem is
            # exactly what a bi-encoder distance cannot separate. The reranked
            # order replaces the RRF order for the top-k truncation; combined_score
            # still carries the RRF value for the audit log.
            candidates = [
                RetrievedChunk(
                    embedding_id=cid,
                    source_filename=self._chunks_by_id[cid]["source_filename"],
                    heading_path=self._chunks_by_id[cid]["heading_path"],
                    loc_ref=self._chunks_by_id[cid]["loc_ref"],
                    text=self._chunks_by_id[cid]["text"],
                    vector_distance=distance_by_id.get(cid),
                    combined_score=rrf_scores[cid],
                )
                for cid in fused_ids
            ]
            top = self._reranker.rerank(query, candidates)[:top_k]
        else:
            top = []
            for cid in fused_ids[:top_k]:
                chunk = self._chunks_by_id[cid]
                top.append(
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
        return top
