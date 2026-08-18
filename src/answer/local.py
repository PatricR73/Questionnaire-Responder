"""Local-model answerer: fully on-premise generation via an OpenAI-compatible chat API.

Pack 3, C7. A meaningful share of this market cannot send internal policy text to a
third-party API at all — regulated industries, government suppliers, and the
security teams most likely to be handed these questionnaires. Everything except
generation is already local (parsing, chunking, embedding, retrieval, the reranker,
the review UI), and the Answerer interface exists precisely to make the generation
step swappable. Ollama and vLLM both expose /v1/chat/completions, so one provider
covers both.

The guarantees are pipeline properties, not model properties, and local mode must
not weaken them:

- The same no-fabrication SYSTEM_PROMPT and answer schema from generate.py are
  reused verbatim, with JSON mode + the same defensive validation and one
  corrective retry (MalformedAnswerError on final failure, per-row isolated
  upstream).
- The verbatim citation cross-check (cross_check_confidence) runs unchanged on
  local output — it is provider-agnostic and never trusts the model.
- The optional entailment check (entailment_check flag) runs as an independent
  second call to the same local endpoint, receiving ONLY the answer and cited
  sentences. Caveat, stated honestly: the local judge is the same local model, so
  it is a weaker judge than the hosted check's separate model — the structural
  isolation (separate call, no question, no chunks) is preserved, not the model
  independence.

Quality will be lower than the hosted Claude path for a given model size, and that
is the point of publishing it: "fully local, here's the accuracy cost" is a better
pitch than silence.
"""

import json
import time
from dataclasses import dataclass

import httpx

from src.answer.answerer import Answerer, AnswerPolarity, AnswerResult, AnswerStatus
from src.answer.confidence import WEAK_MATCH_DISTANCE, GroundedConfidence, cross_check_confidence
from src.answer.entailment import EntailmentResult
from src.answer.generate import (
    MAX_RETRIES,
    REQUIRED_ANSWER_KEYS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_JITTER_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    SYSTEM_PROMPT,
    MalformedAnswerError,
    _build_user_message,
)
from src.retrieval.hybrid_search import RetrievedChunk

DEFAULT_BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
DEFAULT_MODEL = "qwen2.5:7b-instruct"
REQUEST_TIMEOUT_SECONDS = 120.0  # local models are slower than hosted APIs; do not time out a 7B on a laptop


@dataclass(frozen=True)
class LocalConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 0.0  # deterministic output; eval variance is measured, not assumed away


def _usage_from_response(response_json: dict) -> tuple[int, int]:
    """(input_tokens, output_tokens) from either the OpenAI usage shape
    (prompt_tokens/completion_tokens — vLLM, llama.cpp server) or Ollama's
    (prompt_eval_count/eval_count). Local endpoints have no prompt-cache
    accounting, so cache fields stay 0."""
    usage = response_json.get("usage") or {}
    in_tok = usage.get("prompt_tokens") or usage.get("prompt_eval_count") or 0
    out_tok = usage.get("completion_tokens") or usage.get("eval_count") or 0
    return int(in_tok), int(out_tok)


class LocalAnswerer(Answerer):
    """Generation via any OpenAI-compatible local endpoint (Ollama, vLLM, llama.cpp server)."""

    provider_name = "local"

    def __init__(
        self,
        config: LocalConfig | None = None,
        *,
        weak_match_distance: float = WEAK_MATCH_DISTANCE,
        entailment_check: bool = False,
        entailment_model: str | None = None,
    ):
        self._config = config or LocalConfig()
        self._weak_match_distance = weak_match_distance
        self._entailment_check = entailment_check
        self._entailment_model = entailment_model or self._config.model
        self._client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    # -- transport -----------------------------------------------------------

    def _post_chat(self, user_content: str, *, model: str | None = None, max_tokens: int = 1024) -> dict:
        """One POST to /chat/completions; raises httpx.HTTPError on transport/status
        failures (retried by the caller with backoff)."""
        payload = {
            "model": model or self._config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": self._config.temperature,
            "max_tokens": max_tokens,
            "stream": False,
            # Ollama accepts format="json"; vLLM accepts response_format json_object.
            # request both keys — each server ignores the one it does not understand.
            "format": "json",
            "response_format": {"type": "json_object"},
        }
        response = self._client.post(f"{self._config.base_url}/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()

    def _call_with_retries(self, user_content: str, *, max_tokens: int = 1024) -> dict:
        """Full response dict, retrying HTTP errors with exponential backoff + jitter;
        parse errors propagate (they get one corrective retry in answer_question)."""
        attempt = 0
        while True:
            try:
                return self._post_chat(user_content, max_tokens=max_tokens)
            except httpx.HTTPError:
                if attempt >= MAX_RETRIES:
                    raise
                delay = min(RETRY_BASE_DELAY_SECONDS * (2**attempt), RETRY_MAX_DELAY_SECONDS)
                time.sleep(delay + RETRY_JITTER_SECONDS)
                attempt += 1

    @staticmethod
    def _parse_payload(response_json: dict) -> dict:
        """OpenAI response -> validated answer payload.

        Extracts the assistant message content, parses it as JSON, and validates
        the required keys (the same REQUIRED_ANSWER_KEYS the Anthropic path
        enforces). Raises MalformedAnswerError on any failure, so a bad local
        model response is a per-row ERROR upstream — never a silent NOT_FOUND."""
        try:
            content = response_json["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MalformedAnswerError(f"Local response missing choices[0].message.content: {exc}") from exc
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise MalformedAnswerError(f"Local response text is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or not REQUIRED_ANSWER_KEYS.issubset(payload.keys()):
            raise MalformedAnswerError(
                f"Local response JSON missing required keys: {REQUIRED_ANSWER_KEYS - set(payload.keys())}"
            )
        return payload

    def _answer_call(self, user_message: str) -> tuple[dict, int, int]:
        """(validated payload, input_tokens, output_tokens) with one corrective
        retry on malformed output — same discipline as the Anthropic path."""
        response_json = self._call_with_retries(user_message)
        in_tok, out_tok = _usage_from_response(response_json)
        try:
            return self._parse_payload(response_json), in_tok, out_tok
        except (MalformedAnswerError, KeyError, IndexError, json.JSONDecodeError) as first_error:
            corrected = user_message + (
                "\n\nReturn ONLY a single JSON object with exactly the required keys: "
                "supported, answer, cited_sentences, vocab_selection, self_confidence, polarity."
            )
            retry_json = self._call_with_retries(corrected)
            retry_in, retry_out = _usage_from_response(retry_json)
            try:
                return self._parse_payload(retry_json), in_tok + retry_in, out_tok + retry_out
            except Exception as exc:  # noqa: BLE001 — any failure becomes a row ERROR
                raise MalformedAnswerError(
                    f"Local model response did not parse after corrective retry: {exc}"
                ) from first_error

    # -- entailment over the same local endpoint -----------------------------

    def _check_entailment_local(self, answer: str, cited_sentences: list[str]) -> EntailmentResult:
        """The A1 support-check, run against the SAME local endpoint with a JSON-mode
        boolean judgement. Same isolation as the hosted check (only answer +
        citations), same downgrade-only semantics; the honest caveat is that the
        judge is the same local model (no second model is configured), so it is a
        weaker judge than the hosted path's separate model."""
        from src.answer.entailment import SYSTEM_PROMPT as ENT_SYSTEM_PROMPT
        from src.answer.entailment import _build_user_message as ent_user_message

        payload = {
            "model": self._entailment_model,
            "messages": [
                {"role": "system", "content": ENT_SYSTEM_PROMPT},
                {"role": "user", "content": ent_user_message(answer, cited_sentences)},
            ],
            "temperature": self._config.temperature,
            "max_tokens": 256,
            "stream": False,
            "format": "json",
            "response_format": {"type": "json_object"},
        }
        response = self._client.post(f"{self._config.base_url}/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        in_tok, out_tok = _usage_from_response(data)
        try:
            content = data["choices"][0]["message"]["content"]
            verdict = json.loads(content)
            supported = bool(verdict.get("supported", True))
            beyond = verdict.get("beyond_claims") or []
        except (KeyError, IndexError, json.JSONDecodeError, TypeError):
            # An unparseable judge verdict cannot be trusted as a pass: treat as
            # unsupported (downgrade) rather than silently shipping an unchecked
            # answer. This is the conservative direction and matches the A1
            # contract (may only downgrade, never upgrade).
            supported = False
            beyond = ["(entailment judge response did not parse)"]
        return EntailmentResult(
            supported=supported, beyond_claims=list(beyond), input_tokens=in_tok, output_tokens=out_tok
        )

    # -- Answerer contract ----------------------------------------------------

    def answer_question(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        vocab_values: list[str] | None = None,
        row_index: int | None = None,
        prior_answers: list[dict] | None = None,
    ) -> AnswerResult:
        user_message = _build_user_message(question, chunks, vocab_values, prior_answers=prior_answers)
        payload, in_tok, out_tok = self._answer_call(user_message)

        supported = payload["supported"]
        if not supported or payload["self_confidence"] == "none":
            return AnswerResult(
                answer="",
                status=AnswerStatus.NOT_FOUND,
                confidence=None,
                cited_chunk_ids=[],
                provider=self.provider_name,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )

        # Citation grounding: IDENTICAL to the hosted path — never trust the model.
        final_confidence = cross_check_confidence(
            _DraftAdapter(payload),  # type: ignore[arg-type]  # structurally an AnswerDraft
            chunks,
            weak_match_distance=self._weak_match_distance,
        )
        cited_chunk_ids = sorted(final_confidence.cited_ids)

        entailment_input_tokens = 0
        entailment_output_tokens = 0
        if (
            self._entailment_check
            and final_confidence != "none"
            and payload["answer"].strip()
            and payload["cited_sentences"]
        ):
            result = self._check_entailment_local(payload["answer"], payload["cited_sentences"])
            entailment_input_tokens = result.input_tokens
            entailment_output_tokens = result.output_tokens
            if not result.supported:
                final_confidence = GroundedConfidence("none", cited_ids=frozenset(cited_chunk_ids))

        # Vocabulary membership: same runtime assertion as the hosted path.
        vocab_selection = payload["vocab_selection"]
        allowed_vocab = vocab_values or []
        if vocab_selection is not None and (not allowed_vocab or vocab_selection not in allowed_vocab):
            vocab_selection = None
            if final_confidence != "none":
                final_confidence = GroundedConfidence("low", cited_ids=frozenset(cited_chunk_ids))

        if final_confidence == "none":
            return AnswerResult(
                answer="",
                status=AnswerStatus.NOT_FOUND,
                confidence=None,
                cited_chunk_ids=[],
                provider=self.provider_name,
                input_tokens=in_tok,
                output_tokens=out_tok,
                entailment_input_tokens=entailment_input_tokens,
                entailment_output_tokens=entailment_output_tokens,
            )

        polarity_map = {
            "affirms": AnswerPolarity.AFFIRMS,
            "denies": AnswerPolarity.DENIES,
            "partial": AnswerPolarity.PARTIAL,
        }
        polarity_raw = payload.get("polarity")
        polarity = polarity_map.get(polarity_raw) if isinstance(polarity_raw, str) else None
        return AnswerResult(
            answer=payload["answer"],
            status=AnswerStatus.ANSWERED,
            confidence=str(final_confidence),
            cited_chunk_ids=cited_chunk_ids,
            provider=self.provider_name,
            vocab_selection=vocab_selection,
            polarity=polarity,
            cited_sentences=payload["cited_sentences"],
            input_tokens=in_tok,
            output_tokens=out_tok,
            entailment_input_tokens=entailment_input_tokens,
            entailment_output_tokens=entailment_output_tokens,
        )


class _DraftAdapter:
    """Minimal AnswerDraft-shaped object for cross_check_confidence, which reads
    .answer, .cited_sentences, .supported and .self_confidence off the payload."""

    def __init__(self, payload: dict):
        self.answer = payload["answer"]
        self.cited_sentences = payload["cited_sentences"]
        self.supported = payload["supported"]
        self.self_confidence = payload["self_confidence"]
