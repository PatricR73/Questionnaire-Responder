"""Compound question splitting.

Stubbed as a pass-through for slice 1: real questionnaires are not yet confirmed to
have enough multi-part questions to justify a second Claude call on every row. Keeping
this as its own function preserves the pipeline shape (question -> [sub-questions] ->
retrieve -> generate) so a real Claude-based splitter can drop in later without
reshaping retrieval/generation.
"""


def split_question(question_text: str) -> list[str]:
    return [question_text]
