from __future__ import annotations

from datetime import UTC, datetime

from voiceguard.contracts import (
    CanonicalAudio,
    EvaluationContext,
    Outcome,
    SemanticAssessment,
    Transcript,
)
from voiceguard.policies import PaymentCardPolicy, RequiredSemanticPolicy


class Evaluator:
    def __init__(self, assessment: SemanticAssessment) -> None:
        self.assessment = assessment

    def assess(self, **_kwargs) -> SemanticAssessment:
        return self.assessment


def inputs():
    return {
        "context": EvaluationContext(
            tenant_id="tenant-a",
            request_id="request_123456",
            nonce="nonce_123456",
            policy_bundle="strict-v1",
            enforced_languages=("en",),
            requested_language="en-US",
            received_at=datetime.now(UTC),
        ),
        "audio": CanonicalAudio(b"\0\0", 16_000, 100, "a" * 64, "b" * 64),
    }


def test_payment_card_is_deterministically_denied() -> None:
    finding = PaymentCardPolicy().evaluate(
        **inputs(),
        transcript=Transcript("card 4111 1111 1111 1111", "en", 1.0, "stt-v1"),
    )
    assert finding.outcome == Outcome.DENY


def test_semantic_policy_denies_unsafe_and_reviews_low_confidence() -> None:
    unsafe = RequiredSemanticPolicy(
        Evaluator(SemanticAssessment(False, 0.999, ("credential",), "semantic-v1")),
        allow_threshold=0.99,
    ).evaluate(
        **inputs(),
        transcript=Transcript("unsafe", "en", 0.99, "stt-v1"),
    )
    uncertain = RequiredSemanticPolicy(
        Evaluator(SemanticAssessment(True, 0.7, (), "semantic-v1")),
        allow_threshold=0.99,
    ).evaluate(
        **inputs(),
        transcript=Transcript("uncertain", "en", 0.99, "stt-v1"),
    )
    assert unsafe.outcome == Outcome.DENY
    assert uncertain.outcome == Outcome.REVIEW
