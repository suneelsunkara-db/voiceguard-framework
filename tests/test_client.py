from __future__ import annotations

import base64
import io
import wave

import pytest
from voiceguard_client import VoiceGuardRejected, build_request, parse_response


def test_client_builds_completed_pcm16_wav_request() -> None:
    body = build_request(
        model_service="catalog.schema.voiceguard",
        pcm16=b"\0\0" * 800,
        sample_rate_hz=16_000,
        request_id="request_123456",
        nonce="nonce_123456",
        language="en-US",
    )
    url = body["messages"][0]["content"][1]["audio_url"]["url"]
    raw = base64.b64decode(url.removeprefix("data:audio/wav;base64,"))
    with wave.open(io.BytesIO(raw), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
    assert body["stream"] is False


def test_client_releases_only_bound_allow_response() -> None:
    admission = parse_response(
        200,
        {
            "choices": [{"message": {"content": "hello"}}],
            "voiceguard": {
                "schema_version": "voiceguard/decision/v1",
                "outcome": "ALLOW",
                "reason": "safe",
                "request_id": "request_123456",
                "detected_language": "en",
                "policy_version": "policy-v1",
            },
        },
        expected_request_id="request_123456",
    )
    assert admission.transcript == "hello"


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (403, {"voiceguard": {"outcome": "DENY", "reason": "unsafe"}}),
        (503, {"voiceguard": {"outcome": "ERROR", "reason": "unavailable"}}),
        (200, {"choices": [{"message": {"content": "leak"}}]}),
        (
            200,
            {
                "choices": [{"message": {"content": "leak"}}],
                "voiceguard": {
                    "schema_version": "voiceguard/decision/v1",
                    "outcome": "ALLOW",
                    "request_id": "different_request",
                    "detected_language": "en",
                    "policy_version": "policy-v1",
                },
            },
        ),
    ],
)
def test_client_fails_closed(status, payload) -> None:
    with pytest.raises(VoiceGuardRejected):
        parse_response(status, payload, expected_request_id="request_123456")
