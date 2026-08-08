"""Draft a single question's answer from retrieved evidence, using Claude.

This is the module that enforces the project's core guarantee: never assert a
security control the organization has not documented. The system prompt below is the
single point of enforcement for that guarantee — everything else in the pipeline is
plumbing around it. Read it as a contract, not a suggestion.
"""

import json
import os
import time
from dataclasses import dataclass

from src.retrieval.hybrid_search import RetrievedChunk

MODEL = "claude-sonnet-5"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1.0

SYSTEM_PROMPT = """You are drafting one answer to a vendor security questionnaire on behalf of an \
organization. You may use ONLY the evidence text provided to you below in this prompt. You have no \
other source of truth: not general security knowledge, not what a "typical" or "standard" company \
does, not what would be reasonable to assume. If it is not written in the provided evidence text, it \
does not exist for the purposes of this answer.

Follow these rules in order. They are not stylistic preferences — getting this wrong means asserting \
a security control the organization has never documented, which exposes it to contractual and legal \
liability far worse than an incomplete answer.

1. NO EVIDENCE, NO CLAIM. If the provided evidence text does not contain enough information to answer \
the question, you MUST NOT guess, infer from general industry practice, extrapolate from a related \
control, or produce a plausible-sounding answer. In this case: set "supported" to false, set "answer" \
to an empty string, set "cited_sentences" to an empty list, set "vocab_selection" to null, and set \
"self_confidence" to "none". This is the correct, expected output for many questions. It is never a \
failure to say a control was not found.

2. PARTIAL EVIDENCE GETS A PARTIAL, HEDGED ANSWER. If the evidence answers only part of the question, \
answer only the part that is directly supported, explicitly say what is not addressed by the evidence, \
and set "self_confidence" to "low". Do not round a partial answer up to a complete one.

3. FULL EVIDENCE GETS A DIRECT ANSWER. If the evidence clearly and fully answers the question, write \
the answer using only facts stated in the evidence text, and set "self_confidence" to "high".

4. CITATIONS MUST BE VERBATIM. Every entry in "cited_sentences" must be copied character-for-character \
from the provided evidence text — no paraphrasing, no combining two sentences into one, no invented \
citations. If you cannot point to an exact sentence that supports a claim, you cannot make that claim.

5. CONSTRAINED VOCABULARY. If a fixed set of allowed values is provided, set "vocab_selection" to \
exactly one value copied from that list, chosen only if the evidence clearly supports it. Otherwise set \
"vocab_selection" to null — never guess at a vocabulary value to avoid leaving it blank.

6. WHEN UNSURE, BE CONSERVATIVE. Absence of evidence is not evidence of absence — do not claim a \
control is missing, only that it is not documented in what you were given. But undocumented is not \
license to describe it anyway. When genuinely torn between two answers, choose the less committal one.

7. A DOCUMENTED "NO" IS NOT THE SAME AS SILENCE. These are different facts and must be kept distinct: \
(a) the evidence explicitly states the organization does NOT do something ("shared accounts are \
prohibited", "we do not offer a public bug bounty program") — this is supported=true, a real answer, \
and set "polarity" to "denies"; (b) the evidence simply never mentions the topic — this is \
supported=false, "polarity" null, per rule 1. Never let a documented negative collapse into "not \
found" — that erases a fact the organization actually stated. When supported=true and the evidence \
affirms the control/practice exists, set "polarity" to "affirms". When the evidence only partially \
addresses the question (rule 2), set "polarity" to "partial".

8. CONFLICTING EVIDENCE MUST NOT BE RECONCILED. If two or more passages make specific, mutually \
exclusive claims about the same control (e.g. one states passwords rotate every 180 days, another \
states 90 days), you MUST NOT pick one, average them, or silently favor the passage that sounds more \
recent, more specific, or more authoritative — none of that is a fact you were given, it is you \
guessing which document is right. Instead: state both claims plainly in "answer", attribute each to its \
source document, and say outright that the evidence is contradictory. Set "supported" to true, \
"self_confidence" to "low", and "polarity" to "partial" (the closest available category — a \
contradiction is not a coherent, complete answer). "cited_sentences" must include the verbatim \
conflicting sentences from every passage involved, so the grounding check can verify both sides were \
quoted, not synthesized.

You will be given the question and a set of evidence passages, each labeled with its source document \
and location. Use only those passages."""


@dataclass
class AnswerDraft:
    answer: str
    supported: bool
    cited_sentences: list[str]
    vocab_selection: str | None
    self_confidence: str  # "high" | "low" | "none"
    polarity: str | None  # "affirms" | "denies" | "partial" when supported, else None
    input_tokens: int
    output_tokens: int


REQUIRED_ANSWER_KEYS = {"supported", "answer", "cited_sentences", "vocab_selection", "self_confidence", "polarity"}

# Passed via output_config for real structured-output enforcement (constrained decoding
# against this exact schema), not merely a tool-use hint. tool_choice-forced tool calls
# were tried first and still let the model drift out of schema under longer, more
# hedged reasoning (observed directly: 3 of 20 real calls emitted leaked
# "</answer>\n<parameter name=...>" text inside the answer string instead of a proper
# cited_sentences field). output_config.format is the stronger mechanism — the API
# rejects/reformats non-conformant output rather than merely being asked nicely for it.
# Nullable enum fields use anyOf, not a bare "type": [...,"null"] + enum list — the
# structured-output validator rejects the latter combination outright.
_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean", "description": "Whether the provided evidence supports any answer at all."},
        "answer": {"type": "string", "description": "The drafted answer text, or empty string if unsupported."},
        "cited_sentences": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Verbatim sentences copied from the evidence text that support the answer.",
        },
        "vocab_selection": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "One value copied from the allowed vocabulary list, or null.",
        },
        "self_confidence": {
            "type": "string",
            "enum": ["high", "low", "none"],
            "description": "Self-assessed confidence: 'none' if unsupported, 'low' if partial, 'high' if fully supported.",
        },
        "polarity": {
            "anyOf": [{"type": "string", "enum": ["affirms", "denies", "partial"]}, {"type": "null"}],
            "description": "Only meaningful when supported=true: 'affirms' if the evidence confirms the control/practice exists, 'denies' if the evidence explicitly states it does NOT (a documented negative — not the same as no evidence at all), 'partial' if only part of the question is addressed OR the evidence contains an unresolved contradiction (rule 8). Null when supported=false.",
        },
    },
    "required": ["supported", "answer", "cited_sentences", "vocab_selection", "self_confidence", "polarity"],
    "additionalProperties": False,
}

_CORRECTIVE_SUFFIX = (
    "\n\n(Your previous response for this question did not parse as the required JSON object. "
    "Return ONLY a single JSON object with exactly these keys: supported, answer, cited_sentences, "
    "vocab_selection, self_confidence, polarity. No text outside the JSON object, no XML-style tags.)"
)


class MalformedAnswerError(RuntimeError):
    """The model's response didn't parse into the expected answer schema, even after one
    corrective retry. This is a real, permanent property of LLM APIs — not eliminated by
    schema enforcement, only made rare. Callers (pipeline.py) catch this per-row, same as
    any other Answerer failure, and record it as AnswerStatus.ERROR: never crash the
    batch, and never let a malformed response get silently treated as NOT_FOUND — that
    would misrepresent a processing failure as a verified absence of evidence."""


def _extract_answer_payload(response) -> dict:
    text_block = next((block for block in response.content if block.type == "text"), None)
    if text_block is None:
        block_types = [block.type for block in response.content]
        raise MalformedAnswerError(f"No text content block in response (stop_reason={response.stop_reason!r}, blocks={block_types})")

    try:
        payload = json.loads(text_block.text)
    except json.JSONDecodeError as exc:
        raise MalformedAnswerError(f"Response text is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or not REQUIRED_ANSWER_KEYS.issubset(payload.keys()):
        got_keys = set(payload.keys()) if isinstance(payload, dict) else "(not a JSON object)"
        raise MalformedAnswerError(f"Response JSON missing required keys: {REQUIRED_ANSWER_KEYS - (got_keys if isinstance(got_keys, set) else set())}; got: {got_keys}")

    return payload


def _build_user_message(question: str, evidence_chunks: list[RetrievedChunk], vocab_values: list[str] | None) -> str:
    passages = "\n\n".join(
        f"[Passage {i + 1} — {c.source_filename}, {c.heading_path or '(no heading)'}, {c.loc_ref}]\n{c.text}"
        for i, c in enumerate(evidence_chunks)
    )
    vocab_line = f"\nAllowed vocabulary values: {', '.join(vocab_values)}" if vocab_values else ""
    return f"Question: {question}{vocab_line}\n\nEvidence passages:\n\n{passages if passages else '(no evidence passages retrieved)'}"


def generate_answer(
    question: str,
    evidence_chunks: list[RetrievedChunk],
    vocab_values: list[str] | None = None,
) -> AnswerDraft:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot call Claude to generate answers.")

    import anthropic

    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS)
    retryable = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APITimeoutError, anthropic.APIConnectionError)

    def call(user_content: str):
        attempt = 0
        while True:
            try:
                return client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    output_config={"format": {"type": "json_schema", "schema": _ANSWER_SCHEMA}},
                    messages=[{"role": "user", "content": user_content}],
                )
            except retryable:
                if attempt >= MAX_RETRIES:
                    raise
                time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))
                attempt += 1

    base_message = _build_user_message(question, evidence_chunks, vocab_values)
    response = call(base_message)
    total_input_tokens = response.usage.input_tokens
    total_output_tokens = response.usage.output_tokens

    try:
        payload = _extract_answer_payload(response)
    except MalformedAnswerError as first_error:
        # One corrective retry, not more — a schema violation this strict output mode
        # still let through is rare (see _ANSWER_SCHEMA's docstring note); a second
        # failure means something structural, not a one-off sampling fluke, and should
        # surface as a real per-row error rather than be retried indefinitely.
        response = call(base_message + _CORRECTIVE_SUFFIX)
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        try:
            payload = _extract_answer_payload(response)
        except MalformedAnswerError as second_error:
            raise MalformedAnswerError(
                f"Model response for {question!r} did not parse after one corrective retry. "
                f"First attempt: {first_error} Retry attempt: {second_error}"
            ) from second_error

    return AnswerDraft(
        answer=payload["answer"],
        supported=payload["supported"],
        cited_sentences=payload["cited_sentences"],
        vocab_selection=payload["vocab_selection"],
        self_confidence=payload["self_confidence"],
        polarity=payload["polarity"],
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )
