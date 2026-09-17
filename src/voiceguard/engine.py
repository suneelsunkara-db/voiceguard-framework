"""Fail-closed VoiceGuard evaluation orchestration."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from voiceguard.contracts import (
    CanonicalAudio,
    Decision,
    DecisionLedger,
    EvaluationContext,
    Finding,
    Outcome,
    Policy,
    ReasonCode,
    ReplayStore,
    Transcriber,
)

_PRECEDENCE = {
    Outcome.ALLOW: 0,
    Outcome.REVIEW: 1,
    Outcome.DENY: 2,
    Outcome.ERROR: 3,
}


class VoiceGuardEngine:
    def __init__(
        self,
        *,
        transcriber: Transcriber,
        policies: Iterable[Policy],
        supported_languages: Iterable[str],
        policy_version: str,
        replay_store: ReplayStore,
        ledger: DecisionLedger,
        decision_ttl_seconds: int = 60,
    ) -> None:
        self.transcriber = transcriber
        self.policies = tuple(policies)
        if not self.policies:
            raise ValueError("at least one required policy must be configured")
        self.supported_languages = frozenset(
            self._primary_language(item) for item in supported_languages
        )
        if not self.supported_languages:
            raise ValueError("supported_languages cannot be empty")
        self.policy_version = policy_version
        self.decision_ttl = timedelta(seconds=decision_ttl_seconds)
        self.replay_store = replay_store
        self.ledger = ledger

    def evaluate(self, context: EvaluationContext, audio: CanonicalAudio) -> Decision:
        issued_at = datetime.now(UTC)
        expires_at = issued_at + self.decision_ttl
        try:
            claimed = self.replay_store.claim(
                tenant_id=context.tenant_id,
                nonce=context.nonce,
                expires_at=expires_at,
            )
        except Exception as exc:
            return self._record(
                self._decision(
                    context=context,
                    audio=audio,
                    issued_at=issued_at,
                    outcome=Outcome.ERROR,
                    reason_code=ReasonCode.POLICY_UNAVAILABLE,
                    reason=f"replay store failed: {type(exc).__name__}",
                )
            )
        if not claimed:
            return self._record(
                self._decision(
                    context=context,
                    audio=audio,
                    issued_at=issued_at,
                    outcome=Outcome.DENY,
                    reason_code=ReasonCode.REPLAY_DETECTED,
                    reason="request nonce was already consumed",
                )
            )
        try:
            transcript = self.transcriber.transcribe(
                audio,
                requested_language=context.requested_language,
                request_id=context.request_id,
            )
        except Exception as exc:
            return self._record(
                self._decision(
                    context=context,
                    audio=audio,
                    issued_at=issued_at,
                    outcome=Outcome.ERROR,
                    reason_code=ReasonCode.TRANSCRIPTION_FAILED,
                    reason=f"required transcriber failed: {type(exc).__name__}",
                )
            )

        language = self._primary_language(transcript.detected_language)
        transcript_text = unicodedata.normalize("NFKC", transcript.text).strip()
        transcript_hash = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
        tenant_languages = frozenset(
            self._primary_language(item) for item in context.enforced_languages
        )
        if language not in self.supported_languages or language not in tenant_languages:
            return self._record(
                self._decision(
                    context=context,
                    audio=audio,
                    issued_at=issued_at,
                    outcome=Outcome.DENY,
                    reason_code=ReasonCode.UNSUPPORTED_LANGUAGE,
                    reason=f"detected language {language!r} is not enforced",
                    transcript_hash=transcript_hash,
                    detected_language=language,
                )
            )
        requested = self._primary_language(context.requested_language or "")
        if requested and requested != language:
            return self._record(
                self._decision(
                    context=context,
                    audio=audio,
                    issued_at=issued_at,
                    outcome=Outcome.DENY,
                    reason_code=ReasonCode.LOW_CONFIDENCE,
                    reason=(
                        f"requested language {requested!r} does not match "
                        f"detected language {language!r}"
                    ),
                    transcript_hash=transcript_hash,
                    detected_language=language,
                )
            )

        findings: list[Finding] = []
        try:
            for policy in self.policies:
                finding = policy.evaluate(context=context, audio=audio, transcript=transcript)
                if not isinstance(finding, Finding):
                    raise TypeError(f"policy {policy.policy_id} returned an invalid finding")
                findings.append(finding)
        except Exception as exc:
            return self._record(
                self._decision(
                    context=context,
                    audio=audio,
                    issued_at=issued_at,
                    outcome=Outcome.ERROR,
                    reason_code=ReasonCode.POLICY_UNAVAILABLE,
                    reason=f"required policy failed: {type(exc).__name__}",
                    transcript_hash=transcript_hash,
                    detected_language=language,
                    findings=tuple(findings),
                )
            )

        outcome = max((item.outcome for item in findings), key=_PRECEDENCE.__getitem__)
        failed = next((item for item in findings if item.outcome == outcome), findings[0])
        reason_code = ReasonCode.SAFE if outcome == Outcome.ALLOW else ReasonCode.POLICY_VIOLATION
        return self._record(
            self._decision(
                context=context,
                audio=audio,
                issued_at=issued_at,
                outcome=outcome,
                reason_code=reason_code,
                reason=failed.reason,
                transcript_hash=transcript_hash,
                detected_language=language,
                findings=tuple(findings),
                sanitized_transcript=transcript_text if outcome == Outcome.ALLOW else None,
            )
        )

    def _decision(
        self,
        *,
        context: EvaluationContext,
        audio: CanonicalAudio,
        issued_at: datetime,
        outcome: Outcome,
        reason_code: ReasonCode,
        reason: str,
        transcript_hash: str = "",
        detected_language: str | None = None,
        findings: tuple[Finding, ...] = (),
        sanitized_transcript: str | None = None,
    ) -> Decision:
        return Decision(
            schema_version="voiceguard/decision/v1",
            outcome=outcome,
            reason_code=reason_code,
            reason=reason,
            tenant_id=context.tenant_id,
            request_id=context.request_id,
            nonce=context.nonce,
            policy_bundle=context.policy_bundle,
            policy_version=self.policy_version,
            source_audio_sha256=audio.source_sha256,
            canonical_audio_sha256=audio.canonical_sha256,
            transcript_sha256=transcript_hash,
            detected_language=detected_language,
            issued_at=issued_at,
            expires_at=issued_at + self.decision_ttl,
            findings=findings,
            sanitized_transcript=sanitized_transcript,
        )

    def _record(self, decision: Decision) -> Decision:
        self.ledger.record(decision)
        return decision

    @staticmethod
    def _primary_language(tag: str) -> str:
        return (tag or "").split("-", 1)[0].lower()
