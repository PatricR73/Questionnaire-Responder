"""Draft a single question's answer from retrieved evidence, using Claude.

This is the module that enforces the project's core guarantee: never assert a
security control the organization has not documented. The system prompt below is the
single point of enforcement for that guarantee — everything else in the pipeline is
plumbing around it. Read it as a contract, not a suggestion.
"""

import json
import os
import random
import time
from dataclasses import dataclass

from src.retrieval.hybrid_search import RetrievedChunk

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024
# Truncation is retried once at this higher limit (a rule-8 conflicting-evidence
# answer plus several verbatim cited_sentences can legitimately exceed 1024); the
# corrective-suffix retry is NOT used for truncation — same limit, same truncation.
TRUNCATION_RETRY_MAX_TOKENS = 2 * MAX_TOKENS
REQUEST_TIMEOUT_SECONDS = 30.0
# MAX_RETRIES is the TOTAL attempt budget per request: 1 initial attempt + 2
# retries. (The old loop started attempt at 0 and raised at attempt >= 3, which
# silently allowed 4 attempts — the retry policy should say what it means.)
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 30.0
RETRY_JITTER_SECONDS = 0.5

# Eval reproducibility, not a stylistic choice: the Anthropic SDK's default sampling
# temperature is 1.0, so before this constant existed every run was a fresh stochastic
# sample and a one-question delta between two runs was indistinguishable from noise —
# exactly how the 12/20 -> 13/20 RRF-reweighting claim in TUNING_LOG.md was originally
# produced (n=20, single sample). 0 asks the API for the most-likely (greedy) output:
# a deliberate choice for a task where we want the most-likely grounded answer rather
# than varied phrasing. Caveat, verified live against this API: claude-sonnet-5
# deprecates the temperature parameter outright — the server rejects any value with a
# 400 ("temperature is deprecated for this model") — so _create_with_temperature
# probes once per process and omits the parameter for models that reject it (their
# fixed default sampling is already the deterministic, most-likely behaviour), while
# keeping temperature=0 for models that still accept it. Revisit if MODEL changes.
TEMPERATURE = 0
_TEMPERATURE_DEPRECATED: bool = False

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

# C4: rules appended to the user message ONLY when a prior approved answer exists.
# Kept out of SYSTEM_PROMPT on purpose — the baseline prompt stays byte-identical for
# rows without candidates, so the published eval numbers are unaffected, and the
# prompt cache key only varies per row-type. Read as a continuation of SYSTEM_PROMPT's
# rules 1-8 (rule 9).
PRIOR_ANSWER_RULES = (
    "9. A PRIOR APPROVED ANSWER IS A CANDIDATE, NOT EVIDENCE. You may be shown a previously "
    "human-approved answer to a similar question, clearly labelled as such with its provenance. "
    "It is NOT a source of truth and it is NOT part of the evidence: it may be stale, superseded "
    "by a policy change, or simply wrong. Use it only as a starting point, and only to the extent "
    "that every claim it makes is directly supported by the CURRENT evidence passages. Never cite "
    "the prior answer itself, never copy its claims into cited_sentences, and if the current "
    "evidence contradicts it or no longer supports it, ignore it entirely and answer from the "
    "evidence alone. The citation, grounding, and entailment checks that run after you are judged "
    "against the CURRENT evidence, never against the prior answer."
)

# The system prompt is identical for every row of a run (~1.5k tokens), so it is sent
# as an explicit block list marked for ephemeral prompt caching: the API serves it
# from cache on rows 2..N instead of re-sending it. Verified live that the API
# accepts the block form and reports cache_creation/cache_read_input_tokens in usage.
# The same prompt text as the string form, so SYSTEM_PROMPT stays the single source
# of truth for the contract it documents.
_SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


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
    # Cache accounting (see _SYSTEM_BLOCKS): cache_read_input_tokens are served from
    # the prompt cache (much cheaper than re-sending); cache_creation_input_tokens
    # are the first-write cost. Carried so the run summary can report money actually
    # spent instead of the nominal input total.
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


REQUIRED_ANSWER_KEYS = {"supported", "answer", "cited_sentences", "vocab_selection", "self_confidence", "polarity"}


def build_answer_schema(vocab_values: list[str] | None = None) -> dict:
    """Structured-output schema for ONE request (passed via output_config, so the API
    enforces it with constrained decoding rather than merely being asked nicely — see
    the note on output_config in the commit that introduced it).

    Built per request instead of as a module constant because the vocab_selection
    branch is constrained to THIS question's actual vocabulary list: constrained
    decoding can only enforce an enum that exists in the schema, and the allowed
    values differ per column/workbook. With a non-empty list the enum is injected so
    the API rejects any other value outright; without one the branch stays a
    free-form nullable string (the model is expected to return null, per system-prompt
    rule 5 — AnthropicAnswerer backstops that expectation at runtime). Nullable enum
    fields use anyOf, not a bare "type": [...,"null"] + enum list — the structured-
    output validator rejects the latter combination outright."""
    vocab_branch: dict = {"type": "string"}
    if vocab_values:
        vocab_branch = {"type": "string", "enum": list(vocab_values)}
    return {
        "type": "object",
        "properties": {
            "supported": {
                "type": "boolean",
                "description": "Whether the provided evidence supports any answer at all.",
            },
            "answer": {"type": "string", "description": "The drafted answer text, or empty string if unsupported."},
            "cited_sentences": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Verbatim sentences copied from the evidence text that support the answer.",
            },
            "vocab_selection": {
                "anyOf": [vocab_branch, {"type": "null"}],
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


class AnswerTruncatedError(RuntimeError):
    """The response hit the max_tokens generation limit and was cut off mid-output.

    Deliberately distinct from MalformedAnswerError: a truncated response is not a
    schema violation, and retrying it with the same limit (the malformed path's
    corrective retry) would truncate again. generate_answer retries ONCE at a higher
    limit and otherwise fails the row cleanly with this diagnosis — callers
    (pipeline.py) record it as AnswerStatus.ERROR, never as NOT_FOUND."""


def _check_truncation(response, max_tokens: int) -> None:
    """Raise AnswerTruncatedError when the model ran out of generation budget.

    stop_reason == "max_tokens" means the output was CUT OFF mid-generation —
    typically a long rule-8 conflicting-evidence answer plus several verbatim
    cited_sentences — so the truncated text is not valid JSON for a real reason
    distinct from malformation, and the corrective-retry path (which exists for
    schema violations) would retry with the SAME limit and truncate again.
    Detected before any JSON parse attempt, so the diagnosis is the cause, not a
    JSON error."""
    if response.stop_reason == "max_tokens":
        raise AnswerTruncatedError(
            f"Response was truncated by the max_tokens limit ({max_tokens}): stop_reason=max_tokens "
            f"with the output cut off mid-generation. A longer limit is needed (long conflicting-"
            f"evidence answers plus several verbatim cited_sentences exceed 1024 tokens); retrying "
            f"with the same limit would truncate again."
        )


def _extract_answer_payload(response, max_tokens: int = MAX_TOKENS) -> dict:
    _check_truncation(response, max_tokens)
    text_block = next((block for block in response.content if block.type == "text"), None)
    if text_block is None:
        block_types = [block.type for block in response.content]
        raise MalformedAnswerError(
            f"No text content block in response (stop_reason={response.stop_reason!r}, blocks={block_types})"
        )

    try:
        payload = json.loads(text_block.text)
    except json.JSONDecodeError as exc:
        raise MalformedAnswerError(f"Response text is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or not REQUIRED_ANSWER_KEYS.issubset(payload.keys()):
        got_keys = set(payload.keys()) if isinstance(payload, dict) else "(not a JSON object)"
        raise MalformedAnswerError(
            f"Response JSON missing required keys: {REQUIRED_ANSWER_KEYS - (got_keys if isinstance(got_keys, set) else set())}; got: {got_keys}"
        )

    return payload


def _build_user_message(
    question: str,
    evidence_chunks: list[RetrievedChunk],
    vocab_values: list[str] | None,
    prior_answers: list[dict] | None = None,
) -> str:
    """Build the user message. prior_answers (C4) are formatted as labelled candidate
    blocks AFTER the evidence, so the "candidate, not evidence" framing (PRIOR_ANSWER_RULES)
    holds in the prompt's actual ordering: the model reads the real evidence first, then
    sees what it may consider as a starting point."""
    passages = "\n\n".join(
        f"[Passage {i + 1} — {c.source_filename}, {c.heading_path or '(no heading)'}, {c.loc_ref}]\n{c.text}"
        for i, c in enumerate(evidence_chunks)
    )
    vocab_line = f"\nAllowed vocabulary values: {', '.join(vocab_values)}" if vocab_values else ""
    prior_block = ""
    if prior_answers:
        from src.answer.library import format_prior_answer_block

        blocks = "\n\n".join(format_prior_answer_block(c) for c in prior_answers)
        prior_block = (
            f"\n\n{PRIOR_ANSWER_RULES}\n\n"
            f"Prior approved answer(s) to consider as candidates only:\n\n{blocks}"
        )
    return (
        f"Question: {question}{vocab_line}\n\nEvidence passages:\n\n{passages if passages else '(no evidence passages retrieved)'}"
        f"{prior_block}"
    )


def generate_answer(
    question: str,
    evidence_chunks: list[RetrievedChunk],
    vocab_values: list[str] | None = None,
    *,
    model: str = MODEL,
    max_tokens: int = MAX_TOKENS,
    prior_answers: list[dict] | None = None,
) -> AnswerDraft:
    """Draft one answer. model/max_tokens default to the module constants (the
    P18 Config passes its resolved values through here); the constants keep their
    comments and stay the documented defaults."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot call Claude to generate answers.")

    import anthropic

    # max_retries=0: the SDK's own retry layer must not sit underneath the
    # hand-rolled loop below — with both active, a rate-limited row could issue
    # roughly a dozen requests. Retry policy lives in exactly one place.
    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
    retryable = (
        anthropic.RateLimitError,
        anthropic.InternalServerError,
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
    )

    def create_message(user_content: str, max_tokens: int = max_tokens, model: str = model):
        """client.messages.create with the reproducibility temperature applied.

        See TEMPERATURE's docstring: some models reject the parameter outright. Probe
        once per process (module-level flag, so a 400-row run pays at most one extra
        rejected call) and omit it for models that deprecate it."""
        global _TEMPERATURE_DEPRECATED
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": TEMPERATURE,
            "system": _SYSTEM_BLOCKS,
            "output_config": {"format": {"type": "json_schema", "schema": build_answer_schema(vocab_values)}},
            "messages": [{"role": "user", "content": user_content}],
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
        """Backoff for one retry: exponential with jitter, honouring Retry-After on
        rate limits (the API tells you when it wants you back; exponential backoff
        alone ignores that signal and can hammer a cooldown). Capped so a hostile
        Retry-After can't stall the run for minutes."""
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
        return min(delay, RETRY_MAX_DELAY_SECONDS) + random.uniform(0, RETRY_JITTER_SECONDS)

    def call(user_content: str, max_tokens: int = max_tokens, model: str = model):
        attempt = 0  # failures so far; the initial attempt is free
        while True:
            try:
                return create_message(user_content, max_tokens=max_tokens, model=model)
            except retryable as exc:
                attempt += 1
                if attempt >= MAX_RETRIES:
                    raise
                time.sleep(retry_delay(attempt, exc))

    base_message = _build_user_message(question, evidence_chunks, vocab_values, prior_answers=prior_answers)
    response = call(base_message)
    total_input_tokens = response.usage.input_tokens
    total_output_tokens = response.usage.output_tokens
    total_cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    total_cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0

    def record_usage(exc: BaseException) -> BaseException:
        """Attach the tokens spent on this row to the exception so the pipeline's
        error path can report real cost — a row that burns two API calls and then
        raises used to report zero tokens."""
        # Dynamic attribute on a BaseException; mypy can't see it, and the
        # pipeline reads it back with getattr(..., "_row_usage", None).
        exc._row_usage = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cache_read_input_tokens": total_cache_read,
            "cache_creation_input_tokens": total_cache_creation,
        }
        return exc

    try:
        payload = _extract_answer_payload(response, MAX_TOKENS)
    except AnswerTruncatedError:
        # Truncation is not malformation: retry ONCE at a higher limit — the
        # corrective-suffix retry would use the same limit and truncate again. If
        # the higher limit also truncates, the second AnswerTruncatedError
        # propagates as the row's real diagnosis (pipeline.py records it as ERROR).
        response = call(base_message, max_tokens=max_tokens * 2)
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        total_cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        total_cache_creation += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        try:
            payload = _extract_answer_payload(response, max_tokens * 2)
        except AnswerTruncatedError as exc:
            raise record_usage(exc)
    except MalformedAnswerError as first_error:
        # One corrective retry, not more — a schema violation this strict output mode
        # still let through is rare (see _ANSWER_SCHEMA's docstring note); a second
        # failure means something structural, not a one-off sampling fluke, and should
        # surface as a real per-row error rather than be retried indefinitely.
        response = call(base_message + _CORRECTIVE_SUFFIX)
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        total_cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        total_cache_creation += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        try:
            payload = _extract_answer_payload(response, MAX_TOKENS)
        except MalformedAnswerError as second_error:
            raise record_usage(
                MalformedAnswerError(
                    f"Model response for {question!r} did not parse after one corrective retry. "
                    f"First attempt: {first_error} Retry attempt: {second_error}"
                )
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
        cache_read_input_tokens=total_cache_read,
        cache_creation_input_tokens=total_cache_creation,
    )
