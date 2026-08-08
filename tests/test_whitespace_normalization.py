"""Regression coverage for a real bug the eval harness surfaced: source markdown files
are hard-wrapped at ~85 characters, so chunk text used to carry a literal newline at
each mid-sentence wrap point. A model quoting a "verbatim" citation naturally
reproduces it with normal single spaces, not the source file's line-wrap points, which
made the byte-exact grounding check in confidence.py fail correct citations outright —
3 of 5 answers in this project's first real API run were silently downgraded to
final_confidence="none" for this reason alone. Fixed by normalizing whitespace once at
chunk-storage time (chunk.py) and again defensively at compare time (confidence.py).
"""

from src.answer.confidence import WEAK_MATCH_DISTANCE, cross_check_confidence
from src.answer.generate import AnswerDraft
from src.ingest.chunk import chunk_blocks, normalize_whitespace
from src.ingest.parse_docs import ParsedBlock
from src.retrieval.hybrid_search import RetrievedChunk


def _draft(cited_sentences: list[str]) -> AnswerDraft:
    return AnswerDraft(
        answer="Yes.",
        supported=True,
        cited_sentences=cited_sentences,
        vocab_selection=None,
        self_confidence="high",
        polarity="affirms",
        input_tokens=0,
        output_tokens=0,
    )


def test_chunk_blocks_collapses_hard_wrap_newlines():
    # Simulates a source file hard-wrapped mid-sentence, exactly like this project's
    # real fixtures: one ParsedBlock per physical line, split at the wrap point.
    blocks = [
        ParsedBlock(heading_path="Encryption", loc_ref="line 1", text="Customers do not have the ability to manage their own encryption keys; all"),
        ParsedBlock(heading_path="Encryption", loc_ref="line 2", text="key management is performed internally by the security team."),
    ]
    chunks = chunk_blocks(blocks, source_filename="doc.md")
    assert len(chunks) == 1
    assert "\n" not in chunks[0].text
    assert "keys; all key management" in chunks[0].text


def test_cross_check_confidence_grounds_citation_across_a_line_wrap():
    chunk = RetrievedChunk(
        embedding_id="doc.md::0",
        source_filename="doc.md",
        heading_path="Encryption",
        loc_ref="line 1",
        text=normalize_whitespace(
            "Customers do not have the ability to manage their own encryption keys; all\n"
            "key management is performed internally by the security team."
        ),
        vector_distance=WEAK_MATCH_DISTANCE - 0.1,
        combined_score=1.0,
    )
    # The model quotes the sentence with normal spacing, as it actually did in the real
    # run this test reproduces — not the source file's line-wrap layout.
    draft = _draft(["Customers do not have the ability to manage their own encryption keys; all key management is performed internally by the security team."])

    assert cross_check_confidence(draft, [chunk]) == "high"


def test_cross_check_confidence_still_rejects_a_paraphrase():
    # The fix is whitespace normalization, not fuzzy matching — a citation that changes
    # the actual wording (not just spacing) must still fail. Guards against the fix
    # accidentally loosening the check into something that would pass an invented claim.
    chunk = RetrievedChunk(
        embedding_id="doc.md::0",
        source_filename="doc.md",
        heading_path="Encryption",
        loc_ref="line 1",
        text="Customers do not have the ability to manage their own encryption keys.",
        vector_distance=WEAK_MATCH_DISTANCE - 0.1,
        combined_score=1.0,
    )
    draft = _draft(["Customers are not permitted to manage their own encryption keys."])

    assert cross_check_confidence(draft, [chunk]) == "none"
