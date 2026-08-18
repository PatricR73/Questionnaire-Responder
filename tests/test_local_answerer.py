"""Tests for the local-model answerer (pack 3, C7): the fully on-premise path.

Uses an in-process HTTP stub so the tests exercise the real request/parse path with
no external server. Covers: happy path with citation grounding, honest abstention,
corrective retry on malformed output, connection-error propagation, vocabulary
enforcement, and the local entailment judge — the guarantees must be no weaker in
local mode, and these tests pin that.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from src.answer.answerer import AnswerStatus
from src.answer.confidence import WEAK_MATCH_DISTANCE
from src.answer.local import LocalAnswerer, LocalConfig
from src.retrieval.hybrid_search import RetrievedChunk

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _chunk(text="All network traffic is encrypted in transit using TLS 1.2 or higher."):
    return RetrievedChunk(
        embedding_id="doc.md::0",
        source_filename="doc.md",
        heading_path="Access Control > Encryption in transit",
        loc_ref="line 10",
        text=text,
        vector_distance=WEAK_MATCH_DISTANCE - 0.1,
        combined_score=1.0,
    )


def _payload(**overrides):
    base = {
        "supported": True,
        "answer": "Yes, traffic is encrypted in transit using TLS 1.2 or higher.",
        "cited_sentences": ["All network traffic is encrypted in transit using TLS 1.2 or higher."],
        "vocab_selection": None,
        "self_confidence": "high",
        "polarity": "affirms",
    }
    base.update(overrides)
    return base


class _StubHandler(BaseHTTPRequestHandler):
    """Serves canned OpenAI-compatible responses; behaviour set per-test via class attr."""

    responses = []  # noqa: RUF012 — per-test mutable canned responses
    status_code = 200
    captured = []  # noqa: RUF012 — per-test request log: {authorization, body}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode()
        self.captured.append({"authorization": self.headers.get("Authorization"), "body": json.loads(raw)})
        body = self.responses.pop(0) if self.responses else {"error": "no canned response"}
        data = json.dumps(body).encode()
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _serve(responses, status_code=200):
    handler = type("H", (_StubHandler,), {"responses": responses, "status_code": status_code, "captured": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    server.handler_class = handler  # tests read handler_class.captured for request assertions
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _answerer(server, api_key=""):
    host, port = server.server_address
    return LocalAnswerer(LocalConfig(base_url=f"http://{host}:{port}/v1", model="test-model", api_key=api_key))


def _openai_response(payload_dict, usage=None):
    return {
        "choices": [{"message": {"content": json.dumps(payload_dict)}}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_local_happy_path_with_grounded_citations():
    server = _serve([_openai_response(_payload())])
    answerer = _answerer(server)
    result = answerer.answer_question("Are communications encrypted in transit?", [_chunk()])
    assert result.status == AnswerStatus.ANSWERED
    assert result.provider == "local"
    assert result.confidence in ("high", "low")
    # Only chunks whose text actually contained a cited sentence count as cited.
    assert result.cited_chunk_ids == ["doc.md::0"]
    assert result.input_tokens == 10 and result.output_tokens == 5
    server.shutdown()


def test_local_honest_abstention_maps_to_not_found():
    server = _serve(
        [_openai_response(_payload(supported=False, answer="", cited_sentences=[], self_confidence="none"))]
    )
    answerer = _answerer(server)
    result = answerer.answer_question("Do you run a bug bounty program?", [_chunk()])
    assert result.status == AnswerStatus.NOT_FOUND
    assert result.confidence is None
    assert result.cited_chunk_ids == []
    server.shutdown()


def test_local_corrective_retry_on_malformed_output():
    # First response: not JSON. Second: valid. One corrective retry, same as the
    # Anthropic path; usage from both calls is accounted.
    server = _serve(
        [
            {
                "choices": [{"message": {"content": "not json at all"}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3},
            },
            _openai_response(_payload(), usage={"prompt_tokens": 11, "completion_tokens": 4}),
        ]
    )
    answerer = _answerer(server)
    result = answerer.answer_question("Are communications encrypted in transit?", [_chunk()])
    assert result.status == AnswerStatus.ANSWERED
    assert result.input_tokens == 20  # 9 + 11
    server.shutdown()


def test_local_malformed_after_retry_is_error():
    server = _serve([{"choices": [{"message": {"content": "nope"}}]}] * 2)
    answerer = _answerer(server)
    from src.answer.generate import MalformedAnswerError

    with pytest.raises(MalformedAnswerError):
        answerer.answer_question("Are communications encrypted in transit?", [_chunk()])
    server.shutdown()


def test_local_connection_error_propagates():
    # Nothing listening on this port: the httpx error must propagate so the
    # pipeline's per-row handler records ERROR — never a silent NOT_FOUND.
    answerer = LocalAnswerer(LocalConfig(base_url="http://127.0.0.1:1/v1", model="m"))
    with pytest.raises(httpx.HTTPError):
        answerer.answer_question("Are communications encrypted in transit?", [_chunk()])


def test_local_vocab_enforcement_downgrades():
    server = _serve([_openai_response(_payload(vocab_selection="Maybe"))])
    answerer = _answerer(server)
    result = answerer.answer_question(
        "Are communications encrypted in transit?", [_chunk()], vocab_values=["Yes", "No"]
    )
    # A value outside the allowed set is dropped and the row downgraded to low.
    assert result.status == AnswerStatus.ANSWERED
    assert result.vocab_selection is None
    assert result.confidence == "low"
    server.shutdown()


def test_local_ungrounded_citations_become_not_found():
    # The model asserts support but cites a sentence that is NOT in the evidence —
    # the verbatim cross-check must catch it exactly as in the hosted path.
    server = _serve([_openai_response(_payload(cited_sentences=["This sentence is invented."]))])
    answerer = _answerer(server)
    result = answerer.answer_question("Are communications encrypted in transit?", [_chunk()])
    assert result.status == AnswerStatus.NOT_FOUND
    server.shutdown()


def test_local_entailment_judge_downgrades():
    # With entailment_check on, the judge gets a second call; an unsupported verdict
    # downgrades to none even though grounding passed.
    server = _serve(
        [
            _openai_response(_payload(), usage={"prompt_tokens": 10, "completion_tokens": 5}),
            {
                "choices": [{"message": {"content": json.dumps({"supported": False, "beyond_claims": ["x"]})}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        ]
    )
    answerer = _answerer(server)
    answerer._entailment_check = True
    result = answerer.answer_question("Are communications encrypted in transit?", [_chunk()])
    assert result.status == AnswerStatus.NOT_FOUND
    assert result.entailment_input_tokens == 4
    server.shutdown()


def test_local_api_key_sends_auth_header_and_hosted_request_shape():
    """With an api key (hosted OpenAI-compatible endpoint: DeepSeek et al.): the
    Authorization header is sent, JSON mode is requested via response_format ONLY
    (the Ollama-only "format" key can 400 on strict hosted APIs), and the prompt
    carries the literal word "json" (DeepSeek's JSON-mode requirement)."""
    server = _serve([_openai_response(_payload())])
    answerer = _answerer(server, api_key="sk-test-123")
    result = answerer.answer_question("Are communications encrypted in transit?", [_chunk()])
    assert result.status == AnswerStatus.ANSWERED
    captured = server.handler_class.captured[0]
    assert captured["authorization"] == "Bearer sk-test-123"
    assert "format" not in captured["body"]
    assert captured["body"]["response_format"] == {"type": "json_object"}
    user_msg = captured["body"]["messages"][1]["content"]
    assert "json" in user_msg.lower()
    server.shutdown()


def test_local_without_key_sends_no_auth_and_keeps_ollama_json_keys():
    """Without a key (Ollama / vLLM / llama.cpp): no Authorization header, and both
    JSON keys are sent — each local server ignores the one it does not understand."""
    server = _serve([_openai_response(_payload())])
    answerer = _answerer(server)
    result = answerer.answer_question("Are communications encrypted in transit?", [_chunk()])
    assert result.status == AnswerStatus.ANSWERED
    captured = server.handler_class.captured[0]
    assert captured["authorization"] is None
    assert captured["body"]["format"] == "json"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    server.shutdown()


def test_is_local_address():
    """The locality check behind the legacy-alias warning: loopback and private
    addresses are local; hosted hostnames (and unresolvable ones) are not — the
    word "local" must never be claimed for an endpoint that cannot be proven
    local."""
    from src.answer.local import _is_local_address

    assert _is_local_address("http://localhost:11434/v1")
    assert _is_local_address("http://127.0.0.1:8000/v1")
    assert _is_local_address("http://[::1]:11434/v1")
    assert _is_local_address("http://10.0.0.5:8080/v1")
    assert _is_local_address("http://192.168.1.10:8000/v1")
    assert _is_local_address("http://172.16.0.5:8000/v1")
    assert not _is_local_address("https://api.deepseek.com/v1")
    assert not _is_local_address("http://no-such-host-xyz.invalid/v1")


def test_local_alias_warns_on_hosted_url(tmp_path, monkeypatch):
    """--provider local (the legacy alias) must warn when pointed at a hosted
    endpoint, because the name promises on-premise; --provider openai-compatible
    (the accurate name) must not warn."""
    from click.testing import CliRunner

    from src.pipeline import cli

    monkeypatch.setenv("QRESP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QRESP_LOCAL_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    base = [
        "answer",
        "--questionnaire",
        str(FIXTURES / "eval" / "questionnaire_eval.xlsx"),
        "--output",
        str(tmp_path / "warn.xlsx"),
        "--limit",
        "0",
        "--dry-run",
    ]
    warned = CliRunner().invoke(cli, base + ["--provider", "local"])
    assert warned.exit_code == 0, warned.output
    assert "legacy name" in warned.output
    clean = CliRunner().invoke(cli, base + ["--provider", "openai-compatible"])
    assert clean.exit_code == 0, clean.output
    assert "legacy name" not in clean.output
