"""Cross-check Claude's self-assessed confidence against retrieval strength and citation grounding.

Two independent checks can only ever downgrade confidence, never upgrade it:
1. Citation grounding: every cited sentence must appear verbatim in the evidence text
   actually retrieved. If it doesn't, the model paraphrased or invented a citation and
   the answer cannot be trusted regardless of what it claims about itself. Grounding is
   checked per chunk — each sentence must appear inside a single chunk's normalized
   text — never against a join of all chunks: joining chunk texts with a newline and
   then collapsing that newline to a space (the pre-fix behaviour) let a "verbatim"
   sentence assembled from the tail of one chunk plus the head of another pass the
   check despite existing in neither.
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


class GroundedConfidence(str):
    """A confidence string ("high"/"low"/"none") that also carries the set of chunk
    embedding_ids whose text contained at least one grounded citation.

    Subclassing str keeps the long-standing contract that cross_check_confidence
    compares equal to a plain confidence string (callers and tests compare it with
    == "high" / == "none"), while the per-chunk grounding work P2 added —
    identifying WHICH retrieved chunks actually contain cited sentences, not just
    whether the answer is grounded — rides along as the cited_ids attribute.
    Callers that need the ids (AnthropicAnswerer, which must record only
    actually-cited chunks in AnswerResult.cited_chunk_ids) read .cited_ids;
    everything else treats the value as the string it always was."""

    cited_ids: frozenset[str] = frozenset()

    def __new__(cls, value: str, *, cited_ids: frozenset[str]):
        obj = super().__new__(cls, value)
        obj.cited_ids = cited_ids
        return obj


def cross_check_confidence(
    draft: AnswerDraft,
    evidence_chunks: list[RetrievedChunk],
    weak_match_distance: float = WEAK_MATCH_DISTANCE,
) -> str:
    """Returns a GroundedConfidence: the confidence string plus which chunks grounded it.

    The confidence rules are unchanged (see the module docstring). The cited ids are
    the embedding_ids of the chunks whose normalized text contained at least one
    cited sentence — the identity the review UI needs to label "cited evidence"
    truthfully, and the signal a future confidence redesign needs to score the
    distance of the *specific cited chunk* rather than the best distance across
    everything retrieved. When the answer is not grounded the returned set is empty
    (or partial — it still reports whatever grounded; callers on the "none" path
    record no citations regardless).

    weak_match_distance defaults to the WEAK_MATCH_DISTANCE module constant (P18
    passes the resolved Config value through); the constant keeps its long comment
    about being an unvalidated placeholder — do not tune it by hand.
    """
    if not draft.supported or draft.self_confidence == "none":
        return GroundedConfidence("none", cited_ids=frozenset())

    # Chunk text is normalized at storage time (src/ingest/chunk.py), but citations are
    # normalized again here defensively — a model-quoted "verbatim" sentence has no
    # source-file line-wrap artifacts to begin with, but may still have incidental extra
    # whitespace, and this check must stay byte-exact-after-normalization, never fuzzy:
    # a citation-fidelity check that tolerates paraphrase would stop catching invention.
    #
    # Grounding is per chunk: a citation must appear inside ONE chunk's normalized
    # text. Joining all chunks and collapsing the join newline to a space (the old
    # behaviour) made a sentence stitched from the tail of one chunk and the head of
    # another look "verbatim" — it is in neither. The legitimate hard-wrap case is
    # unaffected: a sentence wrapping mid-line WITHIN one source file is fully present
    # in that chunk's normalized text.
    normalized_by_id = {c.embedding_id: normalize_whitespace(c.text) for c in evidence_chunks}
    cited = [s for s in draft.cited_sentences if s.strip()]
    grounded_ids: set[str] = set()
    ungrounded: list[str] = []
    for sentence in cited:
        normalized = normalize_whitespace(sentence)
        containing = [cid for cid, text in normalized_by_id.items() if normalized in text]
        if containing:
            grounded_ids.update(containing)
        else:
            ungrounded.append(sentence)

    # A draft that cites nothing (or only whitespace) is as ungrounded as one that
    # cites an invented sentence — "supported" with no evidence quoted is not an
    # answer this project can ship.
    if not cited or ungrounded:
        return GroundedConfidence("none", cited_ids=frozenset(grounded_ids))

    distances = [c.vector_distance for c in evidence_chunks if c.vector_distance is not None]
    best_distance = min(distances) if distances else None
    weak_retrieval = best_distance is not None and best_distance > weak_match_distance

    if draft.self_confidence == "high":
        confidence = "low" if weak_retrieval else "high"
    elif draft.self_confidence == "low":
        confidence = "none" if weak_retrieval else "low"
    else:
        confidence = "none"
    return GroundedConfidence(confidence, cited_ids=frozenset(grounded_ids))
