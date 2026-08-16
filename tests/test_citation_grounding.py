"""P2 regression coverage: citation grounding is checked per chunk, and the cited
chunk identity is recorded.

Two bugs this locks in:

1. The grounding check used to join all chunk texts with "\n" and then normalize the
   join to a space, so a "verbatim" sentence assembled from the tail of one chunk plus
   the head of another passed the check despite existing in neither. Grounding is now
   per chunk: a citation must appear inside one chunk's normalized text.

2. AnthropicAnswerer recorded EVERY retrieved chunk in AnswerResult.cited_chunk_ids,
   which made the review UI display all retrieved passages under "Cited evidence" and
   made the README's v2 priority #1 (distance of the specific cited chunk) impossible
   to implement. It now records only the chunks whose text contained a cited sentence,
   and NOT_FOUND rows return [] — matching StubAnswerer.

The legitimate hard-wrap case (a sentence wrapping mid-line WITHIN one source file)
keeps working; that regression guard lives in test_whitespace_normalization.py and
must pass unchanged.
"""

from src.answer import answerer as answerer_module
from src.answer.answerer import AnswerStatus, AnthropicAnswerer
from src.answer.confidence import WEAK_MATCH_DISTANCE, cross_check_confidence
from src.answer.generate import AnswerDraft
from src.retrieval.hybrid_search import RetrievedChunk

STRONG_DISTANCE = WEAK_MATCH_DISTANCE - 0.1

TAIL_CHUNK = RetrievedChunk(
    embedding_id="doc.md::0",
    source_filename="doc.md",
    heading_path="Access Control",
    loc_ref="line 1",
    text="All network traffic is encrypted in transit using TLS 1.2.",
    vector_distance=STRONG_DISTANCE,
    combined_score=1.0,
)

HEAD_CHUNK = RetrievedChunk(
    embedding_id="doc.md::1",
    source_filename="doc.md",
    heading_path="Access Control",
    loc_ref="line 2",
    text="MFA is required for all administrative accounts.",
    vector_distance=STRONG_DISTANCE,
    combined_score=1.0,
)


def _draft(cited_sentences, self_confidence="high", supported=True) -> AnswerDraft:
    return AnswerDraft(
        answer="Yes.",
        supported=supported,
        cited_sentences=cited_sentences,
        vocab_selection=None,
        self_confidence=self_confidence,
        polarity="affirms",
        input_tokens=0,
        output_tokens=0,
    )


def test_sentence_straddling_a_chunk_boundary_is_rejected():
    # "...TLS 1.2. MFA is required" exists only in the *join* of the two chunks —
    # the pre-fix check collapsed that join newline to a space and accepted it.
    # Neither chunk contains the full sentence, so it must be rejected.
    stitched = "All network traffic is encrypted in transit using TLS 1.2. MFA is required for all administrative accounts."
    assert cross_check_confidence(_draft([stitched]), [TAIL_CHUNK, HEAD_CHUNK]) == "none"


def test_sentence_present_in_a_single_chunk_is_grounded():
    draft = _draft(["All network traffic is encrypted in transit using TLS 1.2."])
    result = cross_check_confidence(draft, [TAIL_CHUNK, HEAD_CHUNK])
    assert result == "high"
    assert result.cited_ids == frozenset({"doc.md::0"})


def test_cited_ids_cover_every_chunk_that_grounded_a_sentence():
    draft = _draft(
        [
            "All network traffic is encrypted in transit using TLS 1.2.",
            "MFA is required for all administrative accounts.",
        ]
    )
    result = cross_check_confidence(draft, [TAIL_CHUNK, HEAD_CHUNK])
    assert result == "high"
    assert result.cited_ids == frozenset({"doc.md::0", "doc.md::1"})


def test_whitespace_only_citations_are_ungrounded():
    # supported=true with nothing but blank citations must not pass grounding —
    # "supported" with no evidence quoted is not an answer this project can ship.
    assert cross_check_confidence(_draft(["   "]), [TAIL_CHUNK]) == "none"


def test_anthropic_answerer_records_only_grounded_chunks(monkeypatch):
    fake_draft = _draft(["All network traffic is encrypted in transit using TLS 1.2."])
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)

    # The second chunk is retrieved but never cited — it must not appear in
    # cited_chunk_ids.
    result = AnthropicAnswerer().answer_question("Is traffic encrypted?", [TAIL_CHUNK, HEAD_CHUNK])
    assert result.status == AnswerStatus.ANSWERED
    assert result.cited_chunk_ids == ["doc.md::0"]


def test_anthropic_answerer_not_found_branch_returns_empty_cited_ids(monkeypatch):
    fake_draft = _draft(["This sentence is not in any retrieved chunk."])
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)

    result = AnthropicAnswerer().answer_question("Is traffic encrypted?", [TAIL_CHUNK, HEAD_CHUNK])
    assert result.status == AnswerStatus.NOT_FOUND
    assert result.cited_chunk_ids == []  # same contract as StubAnswerer's NOT_FOUND
