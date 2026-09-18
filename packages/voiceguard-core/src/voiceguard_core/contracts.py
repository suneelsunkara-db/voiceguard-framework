"""Stable framework contracts with no vendor dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REVIEW = "REVIEW"
    ERROR = "ERROR"


class ReasonCode(StrEnum):
    SAFE = "safe"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    MALFORMED_AUDIO = "malformed_audio"
    LIMIT_EXCEEDED = "limit_exceeded"
    TRANSCRIPTION_FAILED = "transcription_failed"
    POLICY_UNAVAILABLE = "policy_unavailable"
    POLICY_VIOLATION = "policy_violation"
    LOW_CONFIDENCE = "low_confidence"
    REPLAY_DETECTED = "replay_detected"


@dataclass(frozen=True)
class CanonicalAudio:
    pcm16: bytes
    sample_rate_hz: int
    duration_ms: int
    source_sha256: str
    canonical_sha256: str


@dataclass(frozen=True)
class EvaluationContext:
    tenant_id: str
    request_id: str
    nonce: str
    policy_bundle: str
    enforced_languages: tuple[str, ...]
    requested_language: str | None
    received_at: datetime


@dataclass(frozen=True)
class Transcript:
    text: str
    detected_language: str
    confidence: float | None
    model_version: str


@dataclass(frozen=True)
class Finding:
    policy_id: str
    outcome: Outcome
    reason: str
    confidence: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    schema_version: str
    outcome: Outcome
    reason_code: ReasonCode
    reason: str
    tenant_id: str
    request_id: str
    nonce: str
    policy_bundle: str
    policy_version: str
    source_audio_sha256: str
    canonical_audio_sha256: str
    transcript_sha256: str
    detected_language: str | None
    issued_at: datetime
    expires_at: datetime
    findings: tuple[Finding, ...] = ()
    sanitized_transcript: str | None = None


class Transcriber(Protocol):
    def transcribe(
        self,
        audio: CanonicalAudio,
        *,
        requested_language: str | None,
        request_id: str,
    ) -> Transcript: ...


class Policy(Protocol):
    policy_id: str

    def evaluate(
        self,
        *,
        context: EvaluationContext,
        audio: CanonicalAudio,
        transcript: Transcript,
    ) -> Finding: ...


class DecisionLedger(Protocol):
    def record(self, decision: Decision) -> None: ...


class ReplayStore(Protocol):
    def claim(self, *, tenant_id: str, nonce: str, expires_at: datetime) -> bool: ...


@dataclass(frozen=True)
class SemanticAssessment:
    safe: bool
    confidence: float
    categories: tuple[str, ...]
    model_version: str


class SemanticEvaluator(Protocol):
    def assess(
        self,
        *,
        transcript: Transcript,
        policy_bundle: str,
        tenant_id: str,
        request_id: str,
    ) -> SemanticAssessment: ...
