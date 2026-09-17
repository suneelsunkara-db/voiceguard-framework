from __future__ import annotations

import base64
import io
import struct
import wave

import pytest

from voiceguard.audio import AudioLimits, AudioRejected, WavPcm16Normalizer


def wav(*, seconds: float = 0.2, channels: int = 1, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * round(seconds * rate) * channels)
    return buffer.getvalue()


def test_normalizes_pcm_and_hashes_canonical_representation() -> None:
    normalized = WavPcm16Normalizer().normalize(wav())
    assert normalized.sample_rate_hz == 16_000
    assert normalized.duration_ms == 200
    assert len(normalized.source_sha256) == 64
    assert len(normalized.canonical_sha256) == 64
    assert normalized.source_sha256 != normalized.canonical_sha256


@pytest.mark.parametrize(
    "raw",
    [
        b"not-a-wav",
        wav(channels=2),
        wav() + b"<script>polyglot</script>",
        wav(rate=44_100),
    ],
)
def test_rejects_ambiguous_or_unsupported_audio(raw: bytes) -> None:
    with pytest.raises(AudioRejected):
        WavPcm16Normalizer().normalize(raw)


def test_rejects_invalid_declared_riff_size() -> None:
    raw = bytearray(wav())
    raw[4:8] = struct.pack("<I", len(raw))
    with pytest.raises(AudioRejected, match="RIFF size"):
        WavPcm16Normalizer().normalize(bytes(raw))


def test_rejects_noncanonical_base64_and_preallocation_bomb() -> None:
    normalizer = WavPcm16Normalizer(AudioLimits(max_encoded_bytes=100))
    with pytest.raises(AudioRejected, match="canonical"):
        normalizer.decode_base64("%%%%")
    with pytest.raises(AudioRejected, match="encoded"):
        normalizer.decode_base64(base64.b64encode(wav()).decode())
