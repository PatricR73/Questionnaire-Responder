"""Group heading-anchored ParsedBlocks into size-bounded chunks for embedding."""

import re
from dataclasses import dataclass

from src.ingest.parse_docs import ParsedBlock

MAX_CHUNK_CHARS = 1000
MIN_CHUNK_CHARS = 200
# Complete-sentence overlap between consecutive chunks of the same heading. Chunk
# boundaries fall between complete sentences (see chunk_blocks), so nothing
# straddles a boundary in the normal case; the overlap exists so the boundary
# region is present in BOTH chunks — retrieval gets the surrounding context, and a
# citation quoted from the boundary grounds against either chunk, not just one.
OVERLAP_SENTENCES = 1


@dataclass
class Chunk:
    source_filename: str
    heading_path: str
    loc_ref: str
    text: str


def _combine_loc_refs(first: str, last: str) -> str:
    if first == last:
        return first
    match = re.match(r"^(.*\D)(\d+)$", first)
    if match and last.startswith(match.group(1)):
        return f"{first}–{last[len(match.group(1)) :]}"
    return f"{first}–{last}"


def normalize_whitespace(text: str) -> str:
    """Collapse any run of whitespace, including source hard-wrap newlines, to a single space.

    Source documents are routinely hard-wrapped at ~80-90 characters; without this, a
    chunk's stored text carries a literal newline at each mid-sentence wrap point, which
    breaks any exact-text comparison downstream even when the text reads as one normal
    sentence. Confirmed directly: a model quoting a "verbatim" citation naturally
    reproduces it with normal single spaces, not the source file's line-wrap points, so
    the citation-grounding check in confidence.py was failing correct citations outright.
    Applied once, here, at storage time — not just at the comparison site in
    confidence.py — so every downstream consumer of chunk text (xlsx write-back, a future
    review UI, this check) sees the same normalized form instead of each needing its own
    ad hoc whitespace handling.
    """
    return re.sub(r"\s+", " ", text).strip()


# A "complete sentence" boundary for splitting: sentence-final punctuation followed by
# whitespace. Deliberately simple (no abbreviation handling) — this is a chunking
# heuristic, not a parser, and its only jobs are (a) never to cut a chunk mid-sentence
# when the sentence fits the ceiling, and (b) to pick overlap sentences. Content with no
# sentence-final punctuation at all is treated as one long "sentence" and falls to the
# hard-split path, which still enforces the ceiling.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split an oversized single sentence into pieces that each fit the ceiling.

    Falls back to word-boundary splitting — a sentence longer than the ceiling cannot
    be kept whole in any chunk, and the ceiling is the hard invariant (a chunk that
    overruns it silently breaks WEAK_MATCH_DISTANCE-based reasoning about chunk size).
    Word boundaries keep the pieces readable; a character-level split would be the
    only alternative for a single unbroken token, which does not occur in real policy
    text."""
    pieces: list[str] = []
    words = text.split(" ")
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                pieces.append(current)
            # A single word longer than the ceiling itself: hard cut it.
            while len(word) > max_chars:
                pieces.append(word[:max_chars])
                word = word[max_chars:]
            current = word
    if current:
        pieces.append(current)
    return pieces


def _drop_front_matter(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    """Drop any leading run of blocks that precede the document's first heading.

    Real evidence packs routinely open with a confidentiality banner, title page, or
    disclaimer before the actual document title/first section — boilerplate, not
    evidence. Left in, it becomes a short, generically-worded chunk that semantically
    brushes up against almost any question and shows up as a false-positive distractor
    in retrieval (observed directly: this project's own synthetic-fixture notice was
    matching in the top-5 results for roughly half of a sample eval question set).
    Only a *leading* run is dropped — heading-less text is unusual anywhere else in a
    document and is assumed to be real content, not front matter.
    """
    index = 0
    while index < len(blocks) and not blocks[index].heading_path:
        index += 1
    return blocks[index:]


def chunk_blocks(
    blocks: list[ParsedBlock],
    source_filename: str,
    max_chars: int = MAX_CHUNK_CHARS,
    min_chars: int = MIN_CHUNK_CHARS,
    overlap_sentences: int = OVERLAP_SENTENCES,
) -> list[Chunk]:
    """Group blocks by heading path, then assemble complete sentences into chunks.

    Three properties, in order of importance:

    1. Real ceiling. The old code flushed only AFTER the accumulated text had already
       exceeded max_chars, so every chunk overran by up to one block and a single
       block longer than max_chars was never split at all. Here no chunk's text ever
       exceeds max_chars: sentences are the assembly unit, and a single sentence
       longer than the ceiling is hard-split into fitting pieces (see _hard_split).

    2. No severed sentences. Chunks are assembled from complete sentences, so a
       sentence that spans a block boundary is placed whole in one chunk instead of
       being cut in half — a model quoting that sentence verbatim now grounds against
       the chunk (the citation check in confidence.py requires the full sentence in a
       single chunk's normalized text).

    3. Small overlap of complete sentences between consecutive chunks of the same
       heading (overlap_sentences, default 1): the boundary region appears in both
       chunks, so retrieval and citation-grounding see it from either side. The
       overlap is dropped at a boundary where it would push the next chunk past the
       ceiling — the ceiling is the hard invariant, duplication the soft one; with
       property 2 nothing straddles a boundary regardless.

    normalize_whitespace is applied once per block at assembly time, so stored chunk
    text is the single normalized form every downstream consumer already expects.
    """
    blocks = _drop_front_matter(blocks)

    # Phase 1 — assemble complete sentences into per-heading groups bounded by the
    # ceiling, with the next group's overlap already reserved in the capacity check.
    # Each group is (heading, [content sentences], [loc_ref per content sentence]).
    # pending_overlap holds the last overlap_sentences sentences of the most recent
    # same-heading group, which the NEXT group will prepend at emission; including it
    # in every capacity check is what guarantees the overlap always fits — a chunk
    # packed to the ceiling by the naive check would have no room for it, and the
    # feature would never engage at exactly the boundaries it exists for.
    groups: list[tuple[str | None, list[str], list[str]]] = []
    current_heading: str | None = None
    current_sentences: list[str] = []
    current_locs: list[str] = []
    pending_overlap: list[str] = []

    def push_group():
        nonlocal pending_overlap
        if current_sentences:
            groups.append((current_heading, current_sentences, current_locs))
            if overlap_sentences > 0:
                pending_overlap = _split_sentences(" ".join(current_sentences))[-overlap_sentences:]
            else:
                pending_overlap = []

    def reset():
        nonlocal current_sentences, current_locs
        current_sentences = []
        current_locs = []

    def fits(sentence: str | None) -> bool:
        """True if the accumulated group (including its reserved overlap prefix)
        still fits the ceiling with sentence appended."""
        parts = list(pending_overlap) + list(current_sentences)
        if sentence is not None:
            parts.append(sentence)
        return len(" ".join(parts)) <= max_chars

    for block in blocks:
        if block.heading_path != current_heading:
            push_group()
            reset()
            pending_overlap = []  # overlap never crosses a heading boundary
            current_heading = block.heading_path

        normalized = normalize_whitespace(block.text)
        sentences = _split_sentences(normalized)
        if not sentences:
            continue
        for sentence in sentences:
            if len(sentence) > max_chars:
                # A single sentence over the ceiling: hard-split into its own chunks.
                push_group()
                reset()
                pending_overlap = []
                for piece in _hard_split(sentence, max_chars):
                    groups.append((current_heading, [piece], [block.loc_ref]))
                continue
            if current_sentences and not fits(sentence):
                push_group()
                reset()
                if not fits(sentence):
                    # The reserved overlap alone would push this sentence past the
                    # ceiling — drop the duplication for this boundary (soft
                    # guarantee) rather than violate the ceiling (hard one).
                    pending_overlap = []
            current_sentences.append(sentence)
            current_locs.append(block.loc_ref)
    push_group()

    # Phase 2 — merge a same-heading group that is smaller than min_chars into the
    # previous group, only when the result still fits the ceiling (the old code merged
    # unconditionally, which is one of the ways chunks overran). The incoming group's
    # overlap prefix is dropped on merge — it duplicates the merged chunk's own tail.
    merged: list[tuple[str | None, list[str], list[str]]] = []
    for group in groups:
        if (
            merged
            and merged[-1][0] == group[0]
            and len(" ".join(merged[-1][1])) < min_chars
            and len(" ".join(merged[-1][1] + group[1])) <= max_chars
        ):
            merged[-1][1].extend(group[1])
            merged[-1][2].extend(group[2])
        else:
            merged.append((group[0], list(group[1]), list(group[2])))
    groups = merged

    # Phase 3 — prepend the reserved overlap: the last complete sentences of the
    # previous same-heading chunk, duplicated at the head of this one. The reserve in
    # phase 1 guarantees the result stays within the ceiling; this pass only needs the
    # actual text.
    chunks: list[Chunk] = []
    for heading, sentences, locs in groups:
        text = " ".join(sentences)
        if chunks and chunks[-1].heading_path == heading and overlap_sentences > 0:
            prev_sentences = _split_sentences(chunks[-1].text)
            overlap = prev_sentences[-overlap_sentences:] if prev_sentences else []
            if overlap and overlap != sentences[: len(overlap)]:
                text = " ".join(overlap + sentences)
        loc_ref = _combine_loc_refs(locs[0], locs[-1])
        chunks.append(
            Chunk(
                source_filename=source_filename,
                heading_path=heading or "",
                loc_ref=loc_ref,
                text=text,
            )
        )

    return chunks
