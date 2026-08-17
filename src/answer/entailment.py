"""Third independent confidence layer: does the drafted answer FOLLOW from the cited sentences?

confidence.py verifies that every cited sentence appears verbatim in a retrieved
chunk — a citation-fidelity check. It does not verify that the cited sentences
actually SUPPORT the drafted answer. Those are different properties, and the gap
between them is exactly how ADV-02 (off-site backup storage) and ADV-04 (an IGA
platform for access reviews) got through in the adversarial-subset run: the model
quoted real sentences, passed grounding, and then asserted a control those
sentences merely make plausible. Grounding catches invented citations; it is
demonstrably weaker against over-inference from real ones.

This check closes that gap as a THIRD independent layer, in the same spirit as the
existing two: it may only ever downgrade confidence, never upgrade it.

Isolation is the point: the checker receives ONLY the drafted answer and the cited
sentences — no question, no other retrieved chunks, no evidence the model didn't
cite. A checker that could re-derive the same inference from the same context that
produced it would be checking whether the answer is plausible, not whether it is
stated.

Calibration, deliberately: a claim that ASSERTS a fact about the organization's
controls or practices that the sentences do not state is a violation (ADV-02's
"backups are stored in a separate cloud region" is nowhere in the cited backup
frequency/retention sentences). A statement that the evidence does not address a
topic — an honest abstention like ADV-04's captured answer — is NOT a violation;
flagging abstentions would be the false-positive cost that makes the layer useless.

Behind a config flag (entailment_check, default OFF) so the 14/24 baseline stays
reproducible, using the cheapest capable model (entailment_model, configurable).
The check costs one extra small Claude call per answered row; the pipeline logs
those tokens separately so the price of the guarantee is visible.
"""

import os
import time
from dataclasses import dataclass

from src.answer.generate import (
    _TEMPERATURE_DEPRECATED,
    MAX_RETRIES,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_JITTER_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    TEMPERATURE,
)

# The cheapest capable model for a tiny boolean-entailment judgement. Configurable
# via Config.entailment_model; this is the default. If your account exposes a
# cheaper model tier, set it — the check should cost cents, not dollars.
DEFAULT_ENTAILMENT_MODEL = "claude-sonnet-5"

_ENTAILMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {
            "type": "boolean",
            "description": "False if any factual claim in the answer asserts something the cited sentences do not state.",
        },
        "beyond_claims": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The specific claims that go beyond the cited sentences (empty when supported is true).",
        },
    },
    "required": ["supported", "beyond_claims"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a support-checker, not an answerer. You are given a drafted answer and the
evidence sentences it cites. Decide whether every factual claim in the answer is
STATED in the cited sentences.

Rules:
1. A claim that asserts a fact about the organization's controls, practices, or
   environment that the cited sentences do not state is a violation — even if the
   sentences make that claim plausible. "Plausible" is not "stated".
2. A statement that the evidence does not address some topic (an honest
   abstention, e.g. "the evidence does not mention X") is NOT a violation.
3. If the answer attributes a claim to the evidence ("the evidence states..."),
   verify the evidence actually states it.
4. Answer only with the JSON object. supported must be false if ANY factual claim
   goes beyond the sentences; list the offending claims in beyond_claims."""


@dataclass
class EntailmentResult:
    supported: bool
    beyond_claims: list[str]
    input_tokens: int
    output_tokens: int


class EntailmentCheckError(RuntimeError):
    """The support-check call failed after its retry budget. Raised so the
    pipeline's per-row handler records the row as ERROR — an unchecked answer must
    never ship as if it had been checked, and a silent downgrade would hide a
    checker outage behind a bogus "no support" verdict."""


def _build_user_message(answer: str, cited_sentences: list[str]) -> str:
    """ONLY the answer and the cited sentences — deliberately no question, no
    source filenames, no other chunks (see the module docstring on isolation)."""
    citations = "\n".join(f"- {s}" for s in cited_sentences) if cited_sentences else "(no cited sentences)"
    return f"Drafted answer:\n{answer}\n\nCited evidence sentences:\n{citations}"


def check_answer_entailment(
    answer: str,
    cited_sentences: list[str],
    *,
    model: str = DEFAULT_ENTAILMENT_MODEL,
    max_tokens: int = 256,
) -> EntailmentResult:
    """One cheap Claude call judging whether the answer's claims are all stated in
    the cited sentences. Raises EntailmentCheckError after the retry budget on
    failure (see the class docstring)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot call Claude to check answer support.")

    import anthropic

    client = anthropic.Anthropic(timeout=30.0, max_retries=0)
    retryable = (
        anthropic.RateLimitError,
        anthropic.InternalServerError,
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
    )

    def create_message():
        global _TEMPERATURE_DEPRECATED
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": TEMPERATURE,
            "system": SYSTEM_PROMPT,
            "output_config": {"format": {"type": "json_schema", "schema": _ENTAILMENT_SCHEMA}},
            "messages": [{"role": "user", "content": _build_user_message(answer, cited_sentences)}],
        }
        if _TEMPERATURE_DEPRECATED:
            kwargs.pop("temperature")
            return client.messages.create(**kwargs)
        try:
            return client.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            if "temperature" in str(exc) and "deprecated" in str(exc):
                _TEMPERATURE_DEPRECATED = True
                kwargs.pop("temperature")
                return client.messages.create(**kwargs)
            raise

    def retry_delay(attempt: int, exc: BaseException) -> float:
        if isinstance(exc, anthropic.RateLimitError):
            response = getattr(exc, "response", None)
            headers = response.headers if response is not None else {}
            retry_after = headers.get("retry-after")
            if retry_after:
                try:
                    return min(float(retry_after), RETRY_MAX_DELAY_SECONDS)
                except (TypeError, ValueError):
                    pass
        delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        return min(delay, RETRY_MAX_DELAY_SECONDS) + __import__("random").uniform(0, RETRY_JITTER_SECONDS)

    attempt = 0
    while True:
        try:
            response = create_message()
            break
        except retryable as exc:
            attempt += 1
            if attempt >= MAX_RETRIES:
                raise EntailmentCheckError(
                    f"Support check failed after {MAX_RETRIES} attempts: {type(exc).__name__}: {exc}"
                ) from exc
            time.sleep(retry_delay(attempt, exc))

    import json

    text = next((b for b in response.content if b.type == "text"), None)
    if text is None:
        raise EntailmentCheckError(f"Support check returned no text block (stop_reason={response.stop_reason!r})")
    try:
        payload = json.loads(text.text)
    except json.JSONDecodeError as exc:
        raise EntailmentCheckError(f"Support check returned invalid JSON: {exc}") from exc
    if "supported" not in payload or not isinstance(payload["supported"], bool):
        raise EntailmentCheckError(f"Support check response missing boolean 'supported': {payload!r}")

    return EntailmentResult(
        supported=payload["supported"],
        beyond_claims=payload.get("beyond_claims", []) if isinstance(payload.get("beyond_claims"), list) else [],
        input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
        output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
    )
