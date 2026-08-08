"""Group heading-anchored ParsedBlocks into size-bounded chunks for embedding."""

import re
from dataclasses import dataclass

from src.ingest.parse_docs import ParsedBlock

MAX_CHUNK_CHARS = 1000
MIN_CHUNK_CHARS = 200


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
        return f"{first}–{last[len(match.group(1)):]}"
    return f"{first}–{last}"


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
) -> list[Chunk]:
    """Group blocks by heading path, then split/merge each group to respect size bounds."""
    blocks = _drop_front_matter(blocks)
    chunks: list[Chunk] = []
    current_heading: str | None = None
    current_texts: list[str] = []
    current_locs: list[str] = []

    def flush():
        if not current_texts:
            return
        text = "\n".join(current_texts)
        loc_ref = _combine_loc_refs(current_locs[0], current_locs[-1])
        if chunks and chunks[-1].heading_path == current_heading and len(chunks[-1].text) < min_chars:
            merged = chunks[-1]
            merged.text = f"{merged.text}\n{text}"
            merged.loc_ref = _combine_loc_refs(merged.loc_ref.split("–")[0], loc_ref.split("–")[-1])
            return
        chunks.append(Chunk(source_filename=source_filename, heading_path=current_heading or "", loc_ref=loc_ref, text=text))

    for block in blocks:
        if block.heading_path != current_heading:
            flush()
            current_heading = block.heading_path
            current_texts = []
            current_locs = []

        current_texts.append(block.text)
        current_locs.append(block.loc_ref)

        if sum(len(t) for t in current_texts) >= max_chars:
            flush()
            current_texts = []
            current_locs = []

    flush()
    return chunks
