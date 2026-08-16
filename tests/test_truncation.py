"""P14 regression coverage: max_tokens truncation is detected and diagnosed as its
own failure, not misreported as malformed JSON.

A rule-8 conflicting-evidence answer plus several verbatim cited_sentences can
legitimately exceed 1024 tokens. The truncated output is invalid JSON, which used
to surface as MalformedAnswerError — a misleading diagnosis that also triggered a
corrective retry with the SAME limit, which truncates again. Truncation is now
detected via stop_reason == "max_tokens" before any parse attempt, retried once at
a higher limit, and otherwise fails the row cleanly as AnswerTruncatedError.
"""

import json
from types import SimpleNamespace

import anthropic
import pytest

from src.answer.generate import (
    MAX_TOKENS,
    TRUNCATION_RETRY_MAX_TOKENS,
    AnswerTruncatedError,
    _extract_answer_payload,
    generate_answer,
)

TRUNCATED_TEXT = '{"supported": true, "answer": "This answer is cut off mid-'
VALID_PAYLOAD_TEXT = json.dumps({
    "supported": True,
    "answer": "Complete answer.",
    "cited_sentences": ["A complete sentence."],
    "vocab_selection": None,
    "self_confidence": "high",
    "polarity": "affirms",
})


def _fake_response(text: str, stop_reason: str = "end_turn", input_tokens: int = 100, output_tokens: int = 50):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def test_extract_answer_payload_detects_truncation_before_parsing():
    # stop_reason="max_tokens" must raise AnswerTruncatedError even though the text
    # is invalid JSON — the diagnosis is the cause, not a JSON error.
    with pytest.raises(AnswerTruncatedError, match="max_tokens"):
        _extract_answer_payload(_fake_response(TRUNCATED_TEXT, stop_reason="max_tokens"), 1024)


def test_extract_answer_payload_still_accepts_valid_response():
    payload = _extract_answer_payload(_fake_response(VALID_PAYLOAD_TEXT), 1024)
    assert payload["answer"] == "Complete answer."


def test_generate_answer_retries_truncation_once_at_a_higher_limit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    responses = [
        _fake_response(TRUNCATED_TEXT, stop_reason="max_tokens", input_tokens=90, output_tokens=1000),
        _fake_response(VALID_PAYLOAD_TEXT, input_tokens=110, output_tokens=40),
    ]
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs["max_tokens"])
            return responses.pop(0)

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    draft = generate_answer("Are passwords securely managed?", [])

    # first attempt at the normal limit, retry at the higher limit — never the same
    assert calls == [MAX_TOKENS, TRUNCATION_RETRY_MAX_TOKENS]
    assert draft.answer == "Complete answer."
    # token accounting includes both attempts
    assert draft.input_tokens == 200
    assert draft.output_tokens == 1040


def test_generate_answer_raises_truncated_when_the_higher_limit_also_truncates(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    responses = [
        _fake_response(TRUNCATED_TEXT, stop_reason="max_tokens"),
        _fake_response(TRUNCATED_TEXT, stop_reason="max_tokens"),
    ]

    class FakeMessages:
        def create(self, **kwargs):
            return responses.pop(0)

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    with pytest.raises(AnswerTruncatedError, match="max_tokens"):
        generate_answer("Are passwords securely managed?", [])
