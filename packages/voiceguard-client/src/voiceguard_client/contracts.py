"""Transport-neutral request builder and fail-closed response parser."""

from __future__ import annotations

import base64
import io
import json
import re
import wave
from dataclasses import dataclass
from typing import Any

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{8,128}")
_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")
_SAMPLE_RATES = {8_000, 16_000, 24_000, 48_000}


@dataclass(frozen=True)
class Admission:
    transcript: str
    detected_language: str
    request_id: str
    policy_version: str


class VoiceGuardRejected(RuntimeError):
    def __init__(self, *, status: int, outcome: str, reason: str) -> None:
        self.status = status
        self.outcome = outcome
        self.reason = reason
        super().__init__(f"VoiceGuard {outcome}: {reason}")


def build_request(
    *,
    model_service: str,
    pcm16: bytes,
    sample_rate_hz: int,
    request_id: str,
    nonce: str,
    language: str | None = None,
) -> dict[str, Any]:
    """Build one completed-utterance Chat Completions request."""
    if len(model_service.split(".")) != 3:
        raise ValueError("model_service must be a Unity Catalog FQN")
    if not _IDENTIFIER.fullmatch(request_id) or not _IDENTIFIER.fullmatch(nonce):
        raise ValueError("request_id and nonce must be 8-128 URL-safe characters")
    if language is not None and not _LANGUAGE.fullmatch(language):
        raise ValueError("language must be a simple BCP-47 tag")
    if sample_rate_hz not in _SAMPLE_RATES:
        raise ValueError("sample_rate_hz must be one of 8000, 16000, 24000, or 48000")
    if not pcm16 or len(pcm16) % 2:
        raise ValueError("pcm16 must contain complete signed 16-bit mono samples")

    metadata = {"request_id": request_id, "nonce": nonce}
    if language:
        metadata["language"] = language
    audio = base64.b64encode(_wav(pcm16, sample_rate_hz)).decode("ascii")
    return {
        "model": model_service,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(metadata, separators=(",", ":")),
                    },
                    {
                        "type": "audio_url",
                        "audio_url": {"url": f"data:audio/wav;base64,{audio}"},
                    },
                ],
            }
        ],
    }


def parse_response(
    status: int,
    payload: Any,
    *,
    expected_request_id: str,
) -> Admission:
    """Release transcript only for a bound, structurally valid ALLOW response."""
    if not isinstance(payload, dict):
        raise VoiceGuardRejected(
            status=status,
            outcome="ERROR",
            reason="response was not a JSON object",
        )
    decision = payload.get("voiceguard")
    if not isinstance(decision, dict):
        raise VoiceGuardRejected(
            status=status,
            outcome="ERROR",
            reason="response omitted the VoiceGuard decision",
        )
    outcome = str(decision.get("outcome") or "ERROR")
    reason = str(decision.get("reason") or "request was not admitted")
    if status != 200 or outcome != "ALLOW":
        raise VoiceGuardRejected(status=status, outcome=outcome, reason=reason)
    if decision.get("schema_version") != "voiceguard/decision/v1":
        raise VoiceGuardRejected(
            status=status,
            outcome="ERROR",
            reason="decision schema mismatch",
        )
    if decision.get("request_id") != expected_request_id:
        raise VoiceGuardRejected(
            status=status,
            outcome="ERROR",
            reason="decision request binding mismatch",
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise VoiceGuardRejected(
            status=status,
            outcome="ERROR",
            reason="ALLOW response omitted its single completion",
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    transcript = message.get("content") if isinstance(message, dict) else None
    if not isinstance(transcript, str) or not transcript.strip():
        raise VoiceGuardRejected(
            status=status,
            outcome="ERROR",
            reason="ALLOW response omitted the transcript",
        )
    language = decision.get("detected_language")
    policy_version = decision.get("policy_version")
    if not isinstance(language, str) or not isinstance(policy_version, str):
        raise VoiceGuardRejected(
            status=status,
            outcome="ERROR",
            reason="ALLOW response omitted decision metadata",
        )
    return Admission(
        transcript=transcript,
        detected_language=language,
        request_id=expected_request_id,
        policy_version=policy_version,
    )


def _wav(pcm16: bytes, sample_rate_hz: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate_hz)
        handle.writeframes(pcm16)
    return buffer.getvalue()
