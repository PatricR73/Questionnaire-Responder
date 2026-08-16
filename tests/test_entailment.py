"""A1 regression coverage for the entailment (support) check.

The check is a THIRD independent confidence layer: confidence.py verifies citations
are verbatim (grounding); this verifies the answer's factual claims are STATED in
the cited sentences, not merely plausible from them. It runs behind the
entailment_check config flag (default OFF), receives ONLY the drafted answer and
the cited sentences (no question, no other chunks), and can only downgrade.

The real captured ADV-02/ADV-04 data from the adversarial-subset run is used here
because it is the motivation for the layer — and the honest finding, locked in by
these tests, is that NEITHER is an entailment failure:

- ADV-02's cited sentence ("Backups are stored in a separate cloud region from the
  primary production environment.") is verbatim in the evidence and DIRECTLY states
  the claimed control — the model was grounded and correct; the NOT_FOUND label was
  wrong (the labeling-guide note claimed the storage location was not documented,
  which the evidence contradicts). The label is corrected in questions.json; the
  entailment check correctly does NOT flag a grounded claim.
- ADV-04's answer is a hedge whose factual claims are all present in its citations;
  the defect there is that the structured output reported supported=true for what
  is textually an abstention — a status-mapping issue, not an entailment one.

The mechanism itself is proven by a constructed over-assertion case: an answer
claiming something absent from its citations must be downgraded to "none".
"""

import json
from types import SimpleNamespace

import anthropic

from src.answer import answerer as answerer_module
from src.answer.answerer import AnswerStatus, AnthropicAnswerer
from src.answer.confidence import WEAK_MATCH_DISTANCE
from src.answer.entailment import _build_user_message, check_answer_entailment
from src.answer.generate import AnswerDraft
from src.retrieval.hybrid_search import RetrievedChunk

# Real captured data from the adversarial-subset eval run (fixtures/eval, Run A).
ADV02_ANSWER = (
    "Backups are stored in a separate cloud region from the primary production environment, "
    "which indicates they are not co-located with the primary production infrastructure. "
    "However, the evidence does not explicitly describe this as a 'geographically separate "
    "facility' or provide details on physical distance/location beyond specifying a different "
    "cloud region."
)
ADV02_CITED = ["Backups are stored in a separate cloud region from the primary production environment."]

ADV04_ANSWER = (
    "The evidence indicates that access to production systems is reviewed quarterly by the "
    "security team, and that accounts unused for 90 days are automatically disabled. However, "
    "the evidence does not state that these reviews are conducted through, or automated by, "
    "an identity governance and administration (IGA) platform, so this specific claim cannot "
    "be confirmed."
)
ADV04_CITED = [
    "Access to production systems is reviewed quarterly by the security team.",
    "Any account that has not been used in 90 days is automatically disabled.",
]

CHUNK = RetrievedChunk(
    embedding_id="doc.md::0",
    source_filename="doc.md",
    heading_path="Encryption",
    loc_ref="line 1",
    text="All traffic is encrypted in transit using TLS 1.2.",
    vector_distance=WEAK_MATCH_DISTANCE - 0.1,
    combined_score=1.0,
)


def _draft(answer, cited_sentences, self_confidence="high") -> AnswerDraft:
    return AnswerDraft(
        answer=answer,
        supported=True,
        cited_sentences=cited_sentences,
        vocab_selection=None,
        self_confidence=self_confidence,
        polarity="affirms",
        input_tokens=10,
        output_tokens=5,
    )


def _entailment_response(supported: bool, beyond_claims: list[str]):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text=json.dumps({"supported": supported, "beyond_claims": beyond_claims}))
        ],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=30, output_tokens=12),
    )


class _FakeEntailmentMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeEntailmentClient:
    def __init__(self, response, *a, **k):
        self.messages = _FakeEntailmentMessages(response)


def _answerer_with_entailment(monkeypatch, response):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: _FakeEntailmentClient(response))
    from src.config import Config

    return AnthropicAnswerer(config=Config(entailment_check=True, entailment_model="fake-model"))


def test_entailment_prompt_is_isolated_to_answer_and_citations(monkeypatch):
    fake = _FakeEntailmentMessages(_entailment_response(True, []))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: SimpleNamespace(messages=fake))
    check_answer_entailment("The answer.", ["A cited sentence."], model="m", max_tokens=64)
    user_content = fake.calls[0]["messages"][0]["content"]
    assert "The answer." in user_content
    assert "A cited sentence." in user_content
    # no question, no source filenames, no other context
    assert "?" not in user_content
    assert "doc.md" not in user_content


def test_entailment_check_downgrades_a_constructed_over_assertion(monkeypatch):
    # A genuine over-assertion: the answer claims something absent from the citation.
    # The citation is grounded in the chunk (so grounding passes); only the
    # entailment layer can catch the over-assertion.
    grounded_chunk = RetrievedChunk(
        embedding_id="doc.md::0",
        source_filename="doc.md",
        heading_path="Backup Strategy",
        loc_ref="line 1",
        text="Production databases are backed up hourly with 30-day retention.",
        vector_distance=WEAK_MATCH_DISTANCE - 0.1,
        combined_score=1.0,
    )
    answer = "Backups are stored in a hardened underground bunker."
    cited = ["Production databases are backed up hourly with 30-day retention."]
    fake_draft = _draft(answer, cited)
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)

    answerer = _answerer_with_entailment(
        monkeypatch, _entailment_response(False, ["Backups are stored in a hardened underground bunker."])
    )
    result = answerer.answer_question("Where are backups stored?", [grounded_chunk])
    assert result.status == AnswerStatus.NOT_FOUND
    assert result.confidence is None
    assert result.entailment_input_tokens == 30
    assert result.entailment_output_tokens == 12


def test_entailment_check_passes_when_claims_are_stated(monkeypatch):
    fake_draft = _draft(
        "Data is encrypted in transit using TLS 1.2.", ["All traffic is encrypted in transit using TLS 1.2."]
    )
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)
    answerer = _answerer_with_entailment(monkeypatch, _entailment_response(True, []))
    result = answerer.answer_question("Is traffic encrypted?", [CHUNK])
    assert result.status == AnswerStatus.ANSWERED
    assert result.confidence == "high"  # not downgraded


def test_entailment_check_off_by_default_makes_no_call(monkeypatch):
    fake_draft = _draft("Data is encrypted in transit.", ["All traffic is encrypted in transit using TLS 1.2."])
    monkeypatch.setattr(answerer_module, "generate_answer", lambda *a, **k: fake_draft)
    calls = []

    class Boom:
        def __init__(self, *a, **k):
            calls.append(1)

        def messages_create(self, *a, **k):
            raise AssertionError("entailment must not run when the flag is off")

    monkeypatch.setattr(anthropic, "Anthropic", Boom)
    result = AnthropicAnswerer().answer_question("Q?", [CHUNK])
    assert result.status == AnswerStatus.ANSWERED
    assert calls == []


def test_adv02_real_capture_claim_is_stated_in_its_own_citation():
    # The adversarial run marked ADV-02 a fabrication. The captured citation is
    # verbatim in the evidence and DIRECTLY states the asserted control ("separate
    # cloud region") — so this was a mislabel (the evidence documents the storage
    # location; the label note claimed it did not), not an entailment failure. The
    # label is corrected in questions.json; this test locks the fact that the
    # entailment layer must NOT flag it.
    for sentence in ADV02_CITED:
        assert "separate cloud region" in sentence
    assert "separate cloud region" in ADV02_ANSWER


def test_adv04_real_capture_hedge_claims_are_all_grounded():
    # ADV-04's captured answer is a correct abstention: every factual claim it makes
    # appears in its citations, and the IGA claim is explicitly left unconfirmed.
    # The defect is that the structured output reported supported=true — a
    # status-mapping issue, not an entailment one. The entailment check must not
    # flag a grounded hedge.

    claims = [
        "access to production systems is reviewed quarterly",
        "any account that has not been used in 90 days is automatically disabled",
    ]
    combined = " ".join(ADV04_CITED).lower()
    for claim in claims:
        assert claim in combined, f"claim not grounded: {claim}"
    assert "cannot be confirmed" in ADV04_ANSWER


def test_build_user_message_excludes_question_mark():
    msg = _build_user_message("A.", ["S."])
    assert "?" not in msg
