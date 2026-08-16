"""Contract tests for the Answerer interface: both implementations must return the same
AnswerResult shape, and AnthropicAnswerer must never silently degrade when the API key
is missing — it must raise.
"""

import dataclasses

import pytest

from src.answer import answerer as answerer_module
from src.answer import generate as generate_module
from src.answer.answerer import AnswerPolarity, AnswerResult, AnswerStatus, AnthropicAnswerer, StubAnswerer
from src.answer.confidence import WEAK_MATCH_DISTANCE
from src.retrieval.hybrid_search import RetrievedChunk

STRONG_CHUNK = RetrievedChunk(
    embedding_id="doc.md::0",
    source_filename="doc.md",
    heading_path="Access Control > Encryption in transit",
    loc_ref="line 10",
    text="All network traffic is encrypted in transit using TLS 1.2 or higher.",
    vector_distance=WEAK_MATCH_DISTANCE - 0.1,
    combined_score=1.0,
)

WEAK_CHUNK = RetrievedChunk(
    embedding_id="doc.md::1",
    source_filename="doc.md",
    heading_path="Unrelated Section",
    loc_ref="line 40",
    text="Employee badges must be worn visibly at all times in office premises.",
    vector_distance=WEAK_MATCH_DISTANCE + 0.3,
    combined_score=0.1,
)

RESULT_FIELDS = {f.name for f in dataclasses.fields(AnswerResult)}
REQUIRED_MINIMUM_FIELDS = {"answer", "status", "confidence", "cited_chunk_ids", "provider"}


def test_answer_result_has_required_minimum_fields():
    assert REQUIRED_MINIMUM_FIELDS <= RESULT_FIELDS


def test_stub_answerer_below_threshold_answers():
    result = StubAnswerer().answer_question("Is data encrypted in transit?", [STRONG_CHUNK])
    assert isinstance(result, AnswerResult)
    assert result.status == AnswerStatus.ANSWERED
    assert result.confidence in ("high", "low")
    assert result.cited_chunk_ids == [STRONG_CHUNK.embedding_id]
    assert result.provider == "stub"
    assert STRONG_CHUNK.heading_path in result.answer
    assert result.polarity is None  # stub never judges polarity, only evidence presence


def test_stub_answerer_above_threshold_returns_not_found():
    result = StubAnswerer().answer_question("Do you run a bug bounty program?", [WEAK_CHUNK])
    assert result.status == AnswerStatus.NOT_FOUND
    assert result.confidence is None
    assert result.cited_chunk_ids == []
    assert result.provider == "stub"


def test_stub_answerer_fail_row_raises():
    answerer = StubAnswerer(fail_row=5)
    with pytest.raises(RuntimeError):
        answerer.answer_question("anything", [STRONG_CHUNK], row_index=5)
    # a different row is unaffected
    result = answerer.answer_question("anything", [STRONG_CHUNK], row_index=6)
    assert result.status == AnswerStatus.ANSWERED


def test_anthropic_answerer_matches_stub_shape(monkeypatch):
    fake_draft = generate_module.AnswerDraft(
        answer="Data is encrypted in transit using TLS 1.2 or higher.",
        supported=True,
        cited_sentences=["All network traffic is encrypted in transit using TLS 1.2 or higher."],
        vocab_selection="Yes",
        self_confidence="high",
        polarity=AnswerPolarity.AFFIRMS,
        input_tokens=123,
        output_tokens=45,
    )
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)

    # vocab_values provided so the draft's "Yes" is a legitimate member — membership
    # enforcement (P5) would otherwise downgrade it for being outside any list.
    result = AnthropicAnswerer().answer_question(
        "Is data encrypted in transit?", [STRONG_CHUNK], vocab_values=["Yes", "No"]
    )

    assert isinstance(result, AnswerResult)
    assert set(dataclasses.asdict(result).keys()) == RESULT_FIELDS
    assert result.status == AnswerStatus.ANSWERED
    assert result.confidence == "high"
    assert result.provider == "anthropic"
    assert result.polarity == AnswerPolarity.AFFIRMS
    assert result.input_tokens == 123
    assert result.output_tokens == 45


DENIAL_CHUNK = RetrievedChunk(
    embedding_id="doc.md::2",
    source_filename="doc.md",
    heading_path="Access Control > Authentication",
    loc_ref="line 7",
    text="Shared accounts are prohibited.",
    vector_distance=WEAK_MATCH_DISTANCE - 0.1,
    combined_score=1.0,
)


def test_anthropic_answerer_preserves_documented_negative(monkeypatch):
    """A documented 'no' must stay ANSWERED/DENIES, never collapse into NOT_FOUND."""
    fake_draft = generate_module.AnswerDraft(
        answer="Shared accounts are prohibited.",
        supported=True,
        cited_sentences=["Shared accounts are prohibited."],
        vocab_selection="No",
        self_confidence="high",
        polarity=AnswerPolarity.DENIES,
        input_tokens=100,
        output_tokens=20,
    )
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)

    result = AnthropicAnswerer().answer_question(
        "Do you allow shared accounts?", [DENIAL_CHUNK], vocab_values=["Yes", "No"]
    )

    assert result.status == AnswerStatus.ANSWERED
    assert result.polarity == AnswerPolarity.DENIES
    assert result.answer == "Shared accounts are prohibited."


def test_anthropic_answerer_without_api_key_raises_not_degrades(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicAnswerer().answer_question("Is data encrypted in transit?", [STRONG_CHUNK])
