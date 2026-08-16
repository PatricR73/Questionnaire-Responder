"""Local cross-encoder reranker over the fused candidate pool.

The README's v2 priority #1 correctly diagnoses that a single flat vector-distance
threshold cannot separate "weak but real" from "genuinely absent": the two distance
clusters overlap, so no WEAK_MATCH_DISTANCE value fixes the remaining wrong
answers. This reranker attacks the problem at retrieval time instead of scoring
time: BAAI/bge-reranker-base cross-encodes the question against each candidate
passage and returns a calibrated relevance score, which is a strictly stronger
signal than the bi-encoder distance for the near-miss distractor case.

Deliberately behind a config flag (default OFF) so the existing baseline stays
reproducible, and deliberately retrieval-only: the rerank score is carried onto
RetrievedChunk alongside vector_distance (never overwriting it) and is NOT wired
into cross_check_confidence — the confidence change is a separate commit with its
own data.

The model runs locally (no API cost) and is downloaded on first use (~1.1 GB) —
see the README setup section.
"""

import math

from src.retrieval.hybrid_search import RetrievedChunk

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"


def _sigmoid(x: float) -> float:
    # Stable sigmoid for a 0-1 relevance score (bge-reranker is trained with BCE
    # loss, so the sigmoid of the logit is a relevance probability).
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


class CrossEncoderReranker:
    """Thin wrapper over sentence-transformers' CrossEncoder for the candidate pool.

    Constructing it loads the model (one-time download on first use); prefer a
    single instance per run — the pipeline builds one when the config flag is on."""

    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL, device: str = "cpu"):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, device=device)

    def rerank(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Re-order chunks by question-passage relevance, in place (each chunk's
        rerank_score is set; vector_distance is untouched). Returns the re-ordered
        list. Scores are sigmoid(0-1); with an empty pool this is a no-op."""
        if not chunks:
            return chunks
        pairs = [(query, c.text) for c in chunks]
        logits = self._model.predict(pairs)
        scored = sorted(
            ((float(logit), chunk) for logit, chunk in zip(logits, chunks)),
            key=lambda pair: -pair[0],
        )
        for score, chunk in scored:
            chunk.rerank_score = _sigmoid(score)
        return [chunk for _, chunk in scored]
