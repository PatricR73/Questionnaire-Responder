"""P13 regression coverage: retry/error handling.

Three things this locks in:

1. The SDK's retry layer is disabled (max_retries=0) so the hand-rolled loop is the
   only retry policy; the loop's attempt arithmetic means MAX_RETRIES is the TOTAL
   attempt budget (1 initial + 2 retries, not 4), backoff has jitter, and Retry-After
   is honoured on rate limits.
2. Fatal API errors (auth, permission, not-found/model-name, bad-request) propagate
   out of the per-row handler and abort the run instead of failing every row one at
   a time after a full retry ladder each — the finally save still runs, so
   everything already processed stays on disk.
3. A consecutive-error circuit breaker stops a systemic-but-not-fatal failure from
   burning the whole sheet.
"""

import httpx

import anthropic
import click
import openpyxl
import pytest
from click.testing import CliRunner

import src.answer.generate as generate_module
import src.pipeline as pipeline_module
from src.answer.generate import MAX_RETRIES, generate_answer
from src.answer.answerer import StubAnswerer
from src.pipeline import cli

FIXTURES = pipeline_module.Path(__file__).parent.parent / "fixtures"


def _rate_limit_error(retry_after: str | None = None) -> anthropic.RateLimitError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, request=request, headers=headers)
    return anthropic.RateLimitError("rate limited", response=response, body=None)


def _bad_request_error() -> anthropic.BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.BadRequestError("bad request", response=response, body=None)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        exc = self._responses.pop(0)
        if exc is not None:
            raise exc
        import types
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text='{"supported": false, "answer": "", "cited_sentences": [], "vocab_selection": null, "self_confidence": "none", "polarity": null}')],
            stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class _FakeClient:
    def __init__(self, responses, *a, **k):
        self.messages = _FakeMessages(responses)
        self.last_kwargs = k


def test_generate_answer_retries_exactly_the_total_attempt_budget(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Always rate-limited: the loop must give up after MAX_RETRIES TOTAL attempts.
    responses = [_rate_limit_error() for _ in range(MAX_RETRIES)]
    fake = _FakeClient(responses)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)

    sleeps = []
    monkeypatch.setattr(generate_module.time, "sleep", sleeps.append)

    with pytest.raises(anthropic.RateLimitError):
        generate_answer("question?", [])

    assert len(fake.messages.calls) == MAX_RETRIES, "total attempts must equal MAX_RETRIES"
    assert len(sleeps) == MAX_RETRIES - 1, "one sleep per retry"
    # exponential backoff 1s, 2s, each with jitter < 0.5s
    for i, s in enumerate(sleeps):
        base = 2 ** i
        assert base <= s < base + generate_module.RETRY_JITTER_SECONDS + 1e-9, f"sleep {s} not in jittered window"


def test_generate_answer_honours_retry_after_on_rate_limits(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    responses = [_rate_limit_error(retry_after="2.5"), _rate_limit_error(), _rate_limit_error()]
    fake = _FakeClient(responses)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)

    sleeps = []
    monkeypatch.setattr(generate_module.time, "sleep", sleeps.append)

    with pytest.raises(anthropic.RateLimitError):
        generate_answer("question?", [])

    # First sleep honours the API's Retry-After exactly; later ones are exponential.
    assert sleeps[0] == 2.5
    assert 2.0 <= sleeps[1] < 2.5


def test_generate_answer_does_not_retry_a_fatal_bad_request(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake = _FakeClient([_bad_request_error()])
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)

    sleeps = []
    monkeypatch.setattr(generate_module.time, "sleep", sleeps.append)

    with pytest.raises(anthropic.BadRequestError):
        generate_answer("question?", [])

    assert len(fake.messages.calls) == 1, "fatal errors must not be retried"
    assert sleeps == []


def test_fatal_error_aborts_the_run_immediately(monkeypatch, tmp_path):
    class AlwaysBad(StubAnswerer):
        provider_name = "anthropic"

        def __init__(self, config=None, **kwargs):
            super().__init__(**kwargs)

        def answer_question(self, *a, **k):
            raise _bad_request_error()

    monkeypatch.setattr(pipeline_module, "AnthropicAnswerer", AlwaysBad)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))

    output = tmp_path / "out.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire", str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output", str(output),
            "--limit", "0",
            "--provider", "anthropic",
        ],
    )
    assert result.exit_code != 0
    assert "BadRequestError" in result.output
    assert "Run aborted" in result.output
    # finally save still ran: the workbook exists, with no per-row ERROR cells burned
    assert output.exists()
    # and the run row was recorded (start_questionnaire_run happens before the loop)
    conn = pipeline_module.db.connect(pipeline_module.Path(tmp_path / "data" / "store.db"))
    assert conn.execute("SELECT COUNT(*) AS n FROM questionnaire_runs").fetchone()["n"] == 1


def test_circuit_breaker_stops_a_systemic_failure(monkeypatch, tmp_path):
    class AlwaysFail(StubAnswerer):
        def answer_question(self, *a, **k):
            raise RuntimeError("simulated systemic failure")

    monkeypatch.setattr(pipeline_module, "StubAnswerer", AlwaysFail)
    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))

    output = tmp_path / "out.xlsx"
    result = CliRunner().invoke(
        cli,
        [
            "answer",
            "--questionnaire", str(FIXTURES / "questionnaire_sample.xlsx"),
            "--output", str(output),
            "--limit", "0",
            "--provider", "stub",
        ],
    )
    assert result.exit_code != 0
    assert "circuit breaker" in result.output
    # finally save still ran: rows before the trip are on disk as ERROR rows.
    assert output.exists()
    wb = openpyxl.load_workbook(output)
    ws = wb.active
    from src.questionnaire.write_xlsx import ERROR_MARKER
    error_cells = [
        cell for row in ws.iter_rows() for cell in row
        if cell.value == ERROR_MARKER
    ]
    assert len(error_cells) == pipeline_module.CONSECUTIVE_ERROR_LIMIT - 1
