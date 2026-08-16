"""P12 regression coverage: chunks respect a real size ceiling, no sentence is ever
severed at a chunk boundary, and _drop_front_matter still drops only a leading
heading-less run.

The old chunker flushed only after the accumulated text had already exceeded
MAX_CHUNK_CHARS (every chunk overran by up to one block, and a single block longer
than the ceiling was never split at all) and had no overlap between adjacent chunks,
so a sentence spanning a boundary was cut in half — degrading retrieval and breaking
the verbatim citation-grounding check for a model that quoted that sentence
correctly.
"""

from src.ingest.chunk import MAX_CHUNK_CHARS, chunk_blocks, normalize_whitespace
from src.ingest.parse_docs import ParsedBlock


def _block(heading, text, loc="line 1"):
    return ParsedBlock(heading_path=heading, loc_ref=loc, text=text)


def test_no_chunk_exceeds_the_ceiling():
    # A mix of normal blocks plus one single block far longer than the ceiling.
    blocks = [
        _block("A", "Sentence one here. Sentence two here. " * 20),  # ~640 chars, fits
        _block("A", "Sentence three here. " * 60),  # ~1260 chars — over the ceiling alone
        _block("B", "Short content for heading B."),
    ]
    chunks = chunk_blocks(blocks, source_filename="doc.md")
    assert len(chunks) >= 3
    for chunk in chunks:
        assert len(chunk.text) <= MAX_CHUNK_CHARS, f"chunk of {len(chunk.text)} chars exceeds ceiling: {chunk.text[:80]}..."


def test_sentence_spanning_a_block_boundary_is_fully_present_in_one_chunk():
    # The straddling sentence starts in block 1 and finishes in block 2. The old
    # chunker could cut it in half when the block boundary fell between chunks; the
    # new one assembles complete sentences, so the full sentence lives in one chunk.
    straddle = "This single sentence is deliberately split across two source blocks"
    blocks = [
        _block("A", "Leading sentence one. Leading sentence two. " + straddle),
        _block("A", "but it must be stored whole in exactly one chunk, never severed."),
        _block("A", "Trailing sentence after the boundary."),
    ]
    chunks = chunk_blocks(blocks, source_filename="doc.md")
    full = straddle + " but it must be stored whole in exactly one chunk, never severed."
    assert any(full in c.text for c in chunks), "straddling sentence must be complete in at least one chunk"


def test_overlap_repeats_boundary_sentences_between_same_heading_chunks():
    # Enough sentences that the content spans multiple chunks; the last complete
    # sentence of each chunk must reappear at the start of the next (same heading).
    text = " ".join(f"Sentence number {i} with enough words to fill space. " for i in range(60))
    blocks = [_block("A", text)]
    chunks = chunk_blocks(blocks, source_filename="doc.md")
    assert len(chunks) >= 2
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_tail = normalize_whitespace(prev.text).rsplit(". ", 2)[-1]
        # the last sentence of prev appears at the start of next
        assert prev_tail.rstrip(".") in nxt.text


def test_drop_front_matter_removes_only_a_leading_heading_less_run():
    blocks = [
        _block("", "Confidential — internal use only."),  # front matter
        _block("", "Company Confidential Notice"),
        _block("Access Control", "Real policy content one."),
        _block("", "Heading-less text AFTER the first heading must survive."),
        _block("Access Control", "Real policy content two."),
        _block("Encryption", "Encryption content."),
    ]
    chunks = chunk_blocks(blocks, source_filename="doc.md")
    all_text = " ".join(c.text for c in chunks)
    assert "Confidential — internal use only" not in all_text  # front matter dropped
    assert "Company Confidential Notice" not in all_text
    assert "Heading-less text AFTER the first heading" in all_text  # kept — not front matter
    assert "Real policy content one" in all_text
