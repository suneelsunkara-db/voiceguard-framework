from __future__ import annotations

from datetime import UTC, datetime

import pytest
from voiceguard_core.contracts import (
    CanonicalAudio,
    EvaluationContext,
    Finding,
    Outcome,
    ReasonCode,
    Transcript,
)
from voiceguard_core.engine import VoiceGuardEngine


class Transcriber:
    def __init__(self, *, language: str = "en-US", fail: bool = False) -> None:
        self.language = language
        self.fail = fail

    def transcribe(self, audio, *, requested_language, request_id):
        del audio, requested_language, request_id
        if self.fail:
            raise TimeoutError("offline")
        return Transcript("hello", self.language, 0.99, "stt-v1")


class Policy:
    def __init__(self, outcome: Outcome, *, fail: bool = False) -> None:
        self.policy_id = f"policy-{outcome.value.lower()}"
        self.outcome = outcome
        self.fail = fail

    def evaluate(self, *, context, audio, transcript):
        del context, audio, transcript
        if self.fail:
            raise TimeoutError("offline")
        return Finding(self.policy_id, self.outcome, self.outcome.value.lower())


class ReplayStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.claimed: set[tuple[str, str]] = set()

    def claim(self, *, tenant_id, nonce, expires_at):
        del expires_at
        if self.fail:
            raise TimeoutError("offline")
        key = (tenant_id, nonce)
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True


class Ledger:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.decisions = []

    def record(self, decision):
        if self.fail:
            raise TimeoutError("offline")
        self.decisions.append(decision)


def audio() -> CanonicalAudio:
    return CanonicalAudio(b"\0\0", 16_000, 100, "a" * 64, "b" * 64)


def context(
    *,
    nonce: str = "nonce_123456",
    enforced_languages: tuple[str, ...] = ("en",),
    requested_language: str = "en-US",
) -> EvaluationContext:
    return EvaluationContext(
        tenant_id="tenant-a",
        request_id="request_123456",
        nonce=nonce,
        policy_bundle="strict-v1",
        enforced_languages=enforced_languages,
        requested_language=requested_language,
        received_at=datetime.now(UTC),
    )


def engine(
    *,
    transcriber=None,
    policies=None,
    replay=None,
    ledger=None,
) -> VoiceGuardEngine:
    return VoiceGuardEngine(
        transcriber=transcriber or Transcriber(),
        policies=policies or [Policy(Outcome.ALLOW)],
        supported_languages=["en", "es"],
        policy_version="2026-09-15",
        replay_store=replay or ReplayStore(),
        ledger=ledger or Ledger(),
    )


def test_all_required_policies_must_allow() -> None:
    decision = engine(
        policies=[Policy(Outcome.ALLOW), Policy(Outcome.DENY)],
    ).evaluate(context(), audio())
    assert decision.outcome == Outcome.DENY
    assert decision.sanitized_transcript is None


def test_unknown_language_denies_without_silent_fallback() -> None:
    decision = engine(transcriber=Transcriber(language="zh-CN")).evaluate(context(), audio())
    assert decision.outcome == Outcome.DENY
    assert decision.reason_code == ReasonCode.UNSUPPORTED_LANGUAGE


def test_caller_language_assertion_cannot_force_asr_language() -> None:
    decision = engine(transcriber=Transcriber(language="es-ES")).evaluate(
        context(enforced_languages=("en", "es")),
        audio(),
    )
    assert decision.outcome == Outcome.DENY
    assert decision.reason_code == ReasonCode.LOW_CONFIDENCE


def test_transcriber_policy_and_replay_store_fail_closed() -> None:
    transcription = engine(transcriber=Transcriber(fail=True)).evaluate(context(), audio())
    policy = engine(policies=[Policy(Outcome.ALLOW, fail=True)]).evaluate(context(), audio())
    replay = engine(replay=ReplayStore(fail=True)).evaluate(context(), audio())
    assert {transcription.outcome, policy.outcome, replay.outcome} == {Outcome.ERROR}


def test_nonce_is_consumed_once_per_tenant() -> None:
    replay = ReplayStore()
    instance = engine(replay=replay)
    first = instance.evaluate(context(), audio())
    second = instance.evaluate(context(), audio())
    assert first.outcome == Outcome.ALLOW
    assert second.outcome == Outcome.DENY
    assert second.reason_code == ReasonCode.REPLAY_DETECTED


def test_ledger_failure_never_returns_allow() -> None:
    with pytest.raises(TimeoutError):
        engine(ledger=Ledger(fail=True)).evaluate(context(), audio())
