"""Reusable dependency-free policies."""

from __future__ import annotations

import re

from voiceguard_core.contracts import (
    CanonicalAudio,
    EvaluationContext,
    Finding,
    Outcome,
    SemanticEvaluator,
    Transcript,
)

_DIGIT_RUN = re.compile(r"(?<!\d)(?:\d[\s-]?){13,19}(?!\d)")


class PaymentCardPolicy:
    policy_id = "payment-card-v1"

    def evaluate(
        self,
        *,
        context: EvaluationContext,
        audio: CanonicalAudio,
        transcript: Transcript,
    ) -> Finding:
        del context, audio
        for match in _DIGIT_RUN.finditer(transcript.text):
            digits = re.sub(r"\D", "", match.group())
            if self._luhn(digits):
                return Finding(
                    policy_id=self.policy_id,
                    outcome=Outcome.DENY,
                    reason="payment card number detected",
                )
        return Finding(self.policy_id, Outcome.ALLOW, "no payment card number detected")

    @staticmethod
    def _luhn(digits: str) -> bool:
        if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
            return False
        total = 0
        parity = len(digits) % 2
        for index, char in enumerate(digits):
            value = int(char)
            if index % 2 == parity:
                value = value * 2 - 9 if value > 4 else value * 2
            total += value
        return total % 10 == 0


class RequiredSemanticPolicy:
    """Fail-closed adapter to a governed, route-pinned semantic evaluator."""

    policy_id = "semantic-safety-v1"

    def __init__(self, evaluator: SemanticEvaluator, *, allow_threshold: float) -> None:
        if not 0 < allow_threshold <= 1:
            raise ValueError("allow_threshold must be in (0, 1]")
        self.evaluator = evaluator
        self.allow_threshold = allow_threshold

    def evaluate(
        self,
        *,
        context: EvaluationContext,
        audio: CanonicalAudio,
        transcript: Transcript,
    ) -> Finding:
        del audio
        assessment = self.evaluator.assess(
            transcript=transcript,
            policy_bundle=context.policy_bundle,
            tenant_id=context.tenant_id,
            request_id=context.request_id,
        )
        metadata = {
            "model_version": assessment.model_version,
            "categories": ",".join(assessment.categories),
        }
        if not assessment.safe:
            return Finding(
                self.policy_id,
                Outcome.DENY,
                "semantic policy violation",
                assessment.confidence,
                metadata,
            )
        if assessment.confidence < self.allow_threshold:
            return Finding(
                self.policy_id,
                Outcome.REVIEW,
                "semantic evaluator confidence below allow threshold",
                assessment.confidence,
                metadata,
            )
        return Finding(
            self.policy_id,
            Outcome.ALLOW,
            "semantic safety checks passed",
            assessment.confidence,
            metadata,
        )
