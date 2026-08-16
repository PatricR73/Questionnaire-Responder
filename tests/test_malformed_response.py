"""Regression coverage for a real failure this project hit on its first live Sonnet
run: 3 of 20 real API calls returned a response missing "cited_sentences" entirely,
with what looks like leaked tool-call formatting ("</answer>\n<parameter name=...>")
bleeding into the "answer" field instead. generate_answer must validate the response
shape, retry once with a corrective instruction, and raise a clear, catchable error if
the retry also fails — never crash the batch, never silently treat a malformed
response as a real answer or as NOT_FOUND.

MALFORMED_PAYLOAD_TEXT below is the actual payload captured from that failure
(question IAM-15.1, captured verbatim from the live response), not a hypothetical one.
"""

import json
from types import SimpleNamespace

import pytest

from src.answer.generate import MalformedAnswerError, _extract_answer_payload, generate_answer

# Captured verbatim from a real Claude Sonnet response during this project's first live
# eval run (question IAM-15.1). "cited_sentences" is entirely absent from the JSON;
# what should have been that key's value instead leaked into "answer" as
# tool-call-shaped text ("</answer>\n<parameter name=\"cited_sentences\">...").
MALFORMED_PAYLOAD_TEXT = json.dumps(
    {
        "answer": (
            "The evidence shows conflicting password rotation requirements from two different "
            'source documents. The access_control_policy.md states that "Passwords must be at '
            'least 14 characters and are rotated every 180 days," while it_operations_standards.md '
            'states that "All account passwords must be rotated at least every 90 days." Both '
            "documents describe password management processes (minimum length, rotation, MFA "
            "requirements, prohibition of shared accounts, password reuse blocking), indicating "
            "that processes and technical measures for password management are defined and "
            "implemented, but the specific rotation period is contradictory between the two "
            "documents and cannot be reconciled from the evidence provided. Additionally, no "
            "evidence was found regarding whether these password management processes are "
            'formally "evaluated" (e.g., audited or reviewed).</answer>\n'
            '<parameter name="cited_sentences">["Passwords must be at least 14 characters and '
            'are rotated every 180 days.", "All account passwords must be rotated at least '
            'every 90 days.", "Password reuse across the last 10 passwords is blocked by the '
            'identity provider\'s policy engine.", "Shared accounts are prohibited.", "All '
            "employee accounts require multi-factor authentication (MFA) using a hardware key or "
            'an authenticator app."]'
        ),
        "vocab_selection": None,
        "self_confidence": "low",
        "polarity": "partial",
        "supported": True,
    }
)

VALID_PAYLOAD_TEXT = json.dumps(
    {
        "supported": True,
        "answer": "Passwords rotate every 180 days per one document and every 90 days per another; contradictory.",
        "cited_sentences": ["Passwords must be at least 14 characters and are rotated every 180 days."],
        "vocab_selection": None,
        "self_confidence": "low",
        "polarity": "partial",
    }
)


def _fake_response(text: str, input_tokens: int = 100, output_tokens: int = 50):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def test_extract_answer_payload_rejects_the_real_captured_malformed_response():
    with pytest.raises(MalformedAnswerError, match="cited_sentences"):
        _extract_answer_payload(_fake_response(MALFORMED_PAYLOAD_TEXT))


def test_extract_answer_payload_accepts_a_valid_response():
    payload = _extract_answer_payload(_fake_response(VALID_PAYLOAD_TEXT))
    assert payload["cited_sentences"] == ["Passwords must be at least 14 characters and are rotated every 180 days."]


def test_generate_answer_recovers_via_corrective_retry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    responses = [_fake_response(MALFORMED_PAYLOAD_TEXT, 100, 50), _fake_response(VALID_PAYLOAD_TEXT, 120, 40)]
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs["messages"][0]["content"])
            return responses.pop(0)

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    import anthropic as real_anthropic

    monkeypatch.setattr(real_anthropic, "Anthropic", FakeClient)

    draft = generate_answer("Are passwords securely managed?", [])

    assert draft.polarity == "partial"
    assert draft.cited_sentences  # recovered from the second, valid call
    assert len(calls) == 2
    assert "did not parse" in calls[1]  # second call carried the corrective suffix
    # token accounting includes both attempts, not just the successful one
    assert draft.input_tokens == 220
    assert draft.output_tokens == 90


def test_generate_answer_raises_malformed_after_retry_also_fails(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    responses = [_fake_response(MALFORMED_PAYLOAD_TEXT), _fake_response(MALFORMED_PAYLOAD_TEXT)]

    class FakeMessages:
        def create(self, **kwargs):
            return responses.pop(0)

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = FakeMessages()

    import anthropic as real_anthropic

    monkeypatch.setattr(real_anthropic, "Anthropic", FakeClient)

    # A row that fails twice must raise, not return a fabricated or NOT_FOUND-shaped
    # result — pipeline.py's existing per-row try/except is what turns this into an
    # AnswerStatus.ERROR row without crashing the batch or the rest of the run.
    with pytest.raises(MalformedAnswerError):
        generate_answer("Are passwords securely managed?", [])
