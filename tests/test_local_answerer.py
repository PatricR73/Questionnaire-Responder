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

import httpx
import pytest

from src.answer.answerer import AnswerStatus
from src.answer.confidence import WEAK_MATCH_DISTANCE
from src.answer.local import LocalAnswerer, LocalConfig
from src.retrieval.hybrid_search import RetrievedChunk


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

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
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
    handler = type("H", (_StubHandler,), {"responses": responses, "status_code": status_code})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _answerer(server):
    host, port = server.server_address
    return LocalAnswerer(LocalConfig(base_url=f"http://{host}:{port}/v1", model="test-model"))


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
