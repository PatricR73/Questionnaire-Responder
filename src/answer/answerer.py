"""The Answerer interface: turns (question, retrieved chunks) into an AnswerResult.

status is one of ANSWERED | NOT_FOUND | ERROR and must never be collapsed once set:
NOT_FOUND means "we checked and the evidence doesn't support an answer" (the model's
own honest abstention), ERROR means "we don't know — this row was not successfully
processed" (a retry/network/API failure). Conflating the two would let an
infrastructure failure silently masquerade as a verified absence of evidence, which is
exactly the liability failure mode this project exists to prevent.

Neither implementation raises ERROR itself — both only ever return ANSWERED or
NOT_FOUND, or raise an exception on genuine failure. The caller (pipeline.py) is
responsible for catching that exception per-row and recording it as ERROR; that keeps
the per-row fault isolation in one place instead of duplicated in every Answerer.

status captures evidence-presence + processing outcome (was there evidence at all, did
processing succeed). It deliberately does NOT capture what the evidence says. polarity
is the separate, orthogonal field for that: AFFIRMS (evidence confirms the
control/practice exists), DENIES (evidence explicitly states it does NOT — a
documented negative, e.g. "shared accounts are prohibited"), or PARTIAL (only part of
the question is addressed). polarity is only meaningful when status is ANSWERED; it's
None for NOT_FOUND (no evidence to have a polarity) and ERROR. Collapsing a documented
negative into NOT_FOUND would erase a fact the organization actually stated — "we
don't do X" and "we never said whether we do X" carry different liability weight, and
a security questionnaire eval needs to be able to tell them apart.

vocab_selection and input_tokens/output_tokens are carried on AnswerResult in addition
to the minimum {answer, status, confidence, cited_chunk_ids, provider} contract:
dropping vocab_selection would silently regress write_xlsx's vocab-column write-back,
and dropping token counts would lose the per-question cost visibility needed to decide
whether the answer step needs batching/caching before a 200-400 question run.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.answer.confidence import WEAK_MATCH_DISTANCE, cross_check_confidence
from src.answer.generate import generate_answer
from src.retrieval.hybrid_search import RetrievedChunk


class AnswerStatus:
    ANSWERED = "ANSWERED"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


class AnswerPolarity:
    AFFIRMS = "affirms"
    DENIES = "denies"
    PARTIAL = "partial"


@dataclass
class AnswerResult:
    answer: str
    status: str  # AnswerStatus.ANSWERED | NOT_FOUND | ERROR
    confidence: str | None  # "high" | "low" when ANSWERED, else None
    cited_chunk_ids: list[str]
    provider: str
    vocab_selection: str | None = None
    polarity: str | None = None  # AnswerPolarity.AFFIRMS | DENIES | PARTIAL when ANSWERED, else None
    input_tokens: int = 0
    output_tokens: int = 0


class Answerer(ABC):
    provider_name: str

    @abstractmethod
    def answer_question(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        vocab_values: list[str] | None = None,
        row_index: int | None = None,
    ) -> AnswerResult: ...


class AnthropicAnswerer(Answerer):
    """Existing Claude-backed behavior: generate_answer + cross_check_confidence, unchanged."""

    provider_name = "anthropic"

    def answer_question(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        vocab_values: list[str] | None = None,
        row_index: int | None = None,
    ) -> AnswerResult:
        draft = generate_answer(question, chunks, vocab_values)
        final_confidence = cross_check_confidence(draft, chunks)
        cited_chunk_ids = [c.embedding_id for c in chunks]

        if final_confidence == "none":
            return AnswerResult(
                answer="",
                status=AnswerStatus.NOT_FOUND,
                confidence=None,
                cited_chunk_ids=cited_chunk_ids,
                provider=self.provider_name,
                vocab_selection=None,
                input_tokens=draft.input_tokens,
                output_tokens=draft.output_tokens,
            )

        return AnswerResult(
            answer=draft.answer,
            status=AnswerStatus.ANSWERED,
            confidence=final_confidence,  # "high" or "low"
            cited_chunk_ids=cited_chunk_ids,
            provider=self.provider_name,
            vocab_selection=draft.vocab_selection,
            polarity=draft.polarity,
            input_tokens=draft.input_tokens,
            output_tokens=draft.output_tokens,
        )


class StubAnswerer(Answerer):
    """No network, no model. Branches on the same WEAK_MATCH_DISTANCE threshold the real
    (AnthropicAnswerer -> cross_check_confidence) path uses, so stub runs exercise the
    same retrieval-quality boundary as a real run would. Never sets polarity — it has no
    way to judge whether a chunk affirms or denies anything, only whether one exists."""

    provider_name = "stub"

    def __init__(self, fail_row: int | None = None):
        self.fail_row = fail_row

    def answer_question(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        vocab_values: list[str] | None = None,
        row_index: int | None = None,
    ) -> AnswerResult:
        if self.fail_row is not None and row_index == self.fail_row:
            raise RuntimeError(f"StubAnswerer: simulated failure on row {row_index} (--stub-fail-row)")

        distances = [c.vector_distance for c in chunks if c.vector_distance is not None]
        best_chunk = min(chunks, key=lambda c: c.vector_distance if c.vector_distance is not None else float("inf"), default=None)
        best_distance = min(distances) if distances else None

        if best_distance is not None and best_distance <= WEAK_MATCH_DISTANCE:
            answer = f"[STUB] Per {best_chunk.source_filename} — {best_chunk.heading_path or '(no heading)'}: {best_chunk.text[:200]}"
            return AnswerResult(
                answer=answer,
                status=AnswerStatus.ANSWERED,
                confidence="high",
                cited_chunk_ids=[best_chunk.embedding_id],
                provider=self.provider_name,
                vocab_selection=None,
            )

        return AnswerResult(
            answer="",
            status=AnswerStatus.NOT_FOUND,
            confidence=None,
            cited_chunk_ids=[],
            provider=self.provider_name,
            vocab_selection=None,
        )
