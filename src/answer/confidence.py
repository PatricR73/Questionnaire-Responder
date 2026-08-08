"""Cross-check Claude's self-assessed confidence against retrieval strength and citation grounding.

Two independent checks can only ever downgrade confidence, never upgrade it:
1. Citation grounding: every cited sentence must appear verbatim in the evidence text
   actually retrieved. If it doesn't, the model paraphrased or invented a citation and
   the answer cannot be trusted regardless of what it claims about itself.
2. Retrieval strength: if even the best-matching evidence chunk is a weak semantic
   match to the question, that's the fingerprint of a near-miss distractor (e.g. an
   encryption-in-transit question pulled back an encryption-at-rest passage) and the
   answer should not be trusted at face value.

WEAK_MATCH_DISTANCE is an UNVALIDATED PLACEHOLDER, not a tuned finding. It was set to
0.3 by fitting a single line between two data points from a 6-question, 2-document
fixture set: one no-evidence question at distance ~0.44, and five answerable questions
clustered at 0.14-0.20 (0.5, the original placeholder, sat above both clusters and let
the no-evidence case through as "confident"). That is not enough signal to calibrate a
real threshold, and distance distributions shift as the corpus grows — a value fit to
two documents will not hold on forty. Do not treat 0.3 as validated, and do not tune it
further by hand. The slice-2 eval harness must derive this properly: plot best-match
distance for a real set of known-answerable vs. known-unanswerable questions across a
representative corpus size, and pick the threshold from where that distribution
actually separates.
"""

from src.answer.generate import AnswerDraft
from src.ingest.chunk import normalize_whitespace
from src.retrieval.hybrid_search import RetrievedChunk

WEAK_MATCH_DISTANCE = 0.3


def cross_check_confidence(draft: AnswerDraft, evidence_chunks: list[RetrievedChunk]) -> str:
    if not draft.supported or draft.self_confidence == "none":
        return "none"

    # Chunk text is normalized at storage time (src/ingest/chunk.py), but citations are
    # normalized again here defensively — a model-quoted "verbatim" sentence has no
    # source-file line-wrap artifacts to begin with, but may still have incidental extra
    # whitespace, and this check must stay byte-exact-after-normalization, never fuzzy:
    # a citation-fidelity check that tolerates paraphrase would stop catching invention.
    evidence_text = normalize_whitespace("\n".join(c.text for c in evidence_chunks))
    ungrounded = [s for s in draft.cited_sentences if s.strip() and normalize_whitespace(s) not in evidence_text]
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
