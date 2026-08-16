"""P5 regression coverage: vocab_selection is constrained and validated, never trusted.

The design principle in README.md is that confidence.py "does not trust that
instruction blindly" for citations. Vocabulary selection used to violate that: the
schema typed vocab_selection as an unconstrained string, the prompt merely asked the
model to pick from the list, and whatever came back was written straight into a
data-validated cell — a value outside the sheet's validation list can trigger
Excel's "we found a problem with some content" repair dialog on open.

Enforcement now happens at two independent points, matching citations:
1. The structured-output schema is built per request and injects
   "enum": vocab_values into the vocab_selection branch when a list exists, so
   constrained decoding rejects non-members outright.
2. AnthropicAnswerer asserts membership at runtime. A non-member forces
   vocab_selection to None and downgrades final_confidence to "low" — it is never
   silently dropped and never written.
"""

from src.answer import answerer as answerer_module
from src.answer.answerer import AnswerStatus, AnthropicAnswerer
from src.answer.confidence import WEAK_MATCH_DISTANCE
from src.answer.generate import AnswerDraft, build_answer_schema
from src.retrieval.hybrid_search import RetrievedChunk

CHUNK = RetrievedChunk(
    embedding_id="doc.md::0",
    source_filename="doc.md",
    heading_path="Access Control",
    loc_ref="line 1",
    text="All network traffic is encrypted in transit using TLS 1.2.",
    vector_distance=WEAK_MATCH_DISTANCE - 0.1,
    combined_score=1.0,
)


def _draft(vocab_selection, self_confidence="high") -> AnswerDraft:
    return AnswerDraft(
        answer="Yes.",
        supported=True,
        cited_sentences=["All network traffic is encrypted in transit using TLS 1.2."],
        vocab_selection=vocab_selection,
        self_confidence=self_confidence,
        polarity="affirms",
        input_tokens=0,
        output_tokens=0,
    )


def _schema_vocab_branch(schema):
    return schema["properties"]["vocab_selection"]["anyOf"][0]


def test_schema_with_vocab_list_injects_enum():
    schema = build_answer_schema(["Yes", "No"])
    branch = _schema_vocab_branch(schema)
    assert branch["type"] == "string"
    assert branch["enum"] == ["Yes", "No"]


def test_schema_without_vocab_list_has_no_enum():
    schema = build_answer_schema(None)
    branch = _schema_vocab_branch(schema)
    assert branch["type"] == "string"
    assert "enum" not in branch


def test_schema_with_empty_vocab_list_has_no_enum():
    schema = build_answer_schema([])
    assert "enum" not in _schema_vocab_branch(schema)


def test_member_value_passes_through(monkeypatch):
    fake_draft = _draft(vocab_selection="Yes")
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)
    result = AnthropicAnswerer().answer_question("Is traffic encrypted?", [CHUNK], vocab_values=["Yes", "No"])
    assert result.status == AnswerStatus.ANSWERED
    assert result.vocab_selection == "Yes"
    assert result.confidence == "high"  # not downgraded


def test_non_member_value_is_dropped_and_downgraded(monkeypatch):
    fake_draft = _draft(vocab_selection="Sometimes")
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)
    result = AnthropicAnswerer().answer_question("Is traffic encrypted?", [CHUNK], vocab_values=["Yes", "No"])
    assert result.status == AnswerStatus.ANSWERED
    assert result.vocab_selection is None  # never written
    assert result.confidence == "low"  # downgraded, not silently dropped
    assert result.answer == "Yes."  # the answer itself survives


def test_value_with_no_vocab_list_at_all_is_rejected(monkeypatch):
    # Rule 5: with no allowed list the model must return null — any value is a
    # non-member and must not be written.
    fake_draft = _draft(vocab_selection="Definitely")
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)
    result = AnthropicAnswerer().answer_question("Is traffic encrypted?", [CHUNK], vocab_values=None)
    assert result.vocab_selection is None
    assert result.confidence == "low"
