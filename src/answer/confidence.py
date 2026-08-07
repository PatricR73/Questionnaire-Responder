"""Cross-check Claude's self-assessed confidence against retrieval strength and citation grounding.

Two independent checks can only ever downgrade confidence, never upgrade it:
1. Citation grounding: every cited sentence must appear verbatim in the evidence text
   actually retrieved. If it doesn't, the model paraphrased or invented a citation and
   the answer cannot be trusted regardless of what it claims about itself.
2. Retrieval strength: if even the best-matching evidence chunk is a weak semantic
   match to the question, that's the fingerprint of a near-miss distractor (e.g. an
   encryption-in-transit question pulled back an encryption-at-rest passage) and the
   answer should not be trusted at face value.

WEAK_MATCH_DISTANCE is a placeholder threshold — it has not been tuned against real
data. Tuning it against the slice-2 eval harness's hand-scored set is the intended next
step once there's a labeled sample to tune against.
"""

from src.answer.generate import AnswerDraft
from src.retrieval.hybrid_search import RetrievedChunk

WEAK_MATCH_DISTANCE = 0.5


def cross_check_confidence(draft: AnswerDraft, evidence_chunks: list[RetrievedChunk]) -> str:
    if not draft.supported or draft.self_confidence == "none":
        return "none"

    evidence_text = "\n".join(c.text for c in evidence_chunks)
    ungrounded = [s for s in draft.cited_sentences if s.strip() and s.strip() not in evidence_text]
    if not draft.cited_sentences or ungrounded:
        return "none"

    distances = [c.vector_distance for c in evidence_chunks if c.vector_distance is not None]
    best_distance = min(distances) if distances else None
    weak_retrieval = best_distance is not None and best_distance > WEAK_MATCH_DISTANCE

    if draft.self_confidence == "high":
        return "low" if weak_retrieval else "high"
    if draft.self_confidence == "low":
        return "none" if weak_retrieval else "low"
    return "none"
