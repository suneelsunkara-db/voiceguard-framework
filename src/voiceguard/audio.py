"""Strict audio decoding and canonicalization boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import struct
import wave
from dataclasses import dataclass

from voiceguard.contracts import CanonicalAudio


class AudioRejected(ValueError):
    pass


@dataclass(frozen=True)
class AudioLimits:
    max_encoded_bytes: int = 3_400_000
    max_decoded_bytes: int = 2_500_000
    max_duration_ms: int = 30_000
    min_duration_ms: int = 50
    allowed_sample_rates: frozenset[int] = frozenset({8_000, 16_000, 24_000, 48_000})


class WavPcm16Normalizer:
    """Accept one narrow format and derive a model-independent PCM representation."""

    def __init__(self, limits: AudioLimits | None = None) -> None:
        self.limits = limits or AudioLimits()

    def decode_base64(self, encoded: str) -> CanonicalAudio:
        if not isinstance(encoded, str) or not encoded:
            raise AudioRejected("audio data must be a non-empty base64 string")
        if len(encoded) > self.limits.max_encoded_bytes:
            raise AudioRejected("encoded audio exceeds the configured limit")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AudioRejected("audio is not canonical RFC 4648 base64") from exc
        return self.normalize(raw)

    def normalize(self, raw: bytes) -> CanonicalAudio:
        if len(raw) > self.limits.max_decoded_bytes:
            raise AudioRejected("decoded audio exceeds the configured limit")
        if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            raise AudioRejected("audio is not a RIFF/WAVE container")
        if struct.unpack("<I", raw[4:8])[0] + 8 != len(raw):
            raise AudioRejected("RIFF size mismatch or trailing payload")

        try:
            with wave.open(io.BytesIO(raw), "rb") as handle:
                channels = handle.getnchannels()
                sample_width = handle.getsampwidth()
                sample_rate = handle.getframerate()
                frame_count = handle.getnframes()
                compression = handle.getcomptype()
                frames = handle.readframes(frame_count)
        except (EOFError, wave.Error) as exc:
            raise AudioRejected("malformed WAV container") from exc

        if channels != 1:
            raise AudioRejected("only mono audio is accepted")
        if sample_width != 2 or compression != "NONE":
            raise AudioRejected("only uncompressed PCM16 audio is accepted")
        if sample_rate not in self.limits.allowed_sample_rates:
            raise AudioRejected("sample rate is not allowed")
        expected_frame_bytes = frame_count * channels * sample_width
        if len(frames) != expected_frame_bytes:
            raise AudioRejected("truncated PCM frame data")

        duration_ms = round(frame_count * 1000 / sample_rate) if sample_rate else 0
        if not self.limits.min_duration_ms <= duration_ms <= self.limits.max_duration_ms:
            raise AudioRejected("audio duration is outside the configured bounds")

        canonical_header = struct.pack("<IHH", sample_rate, channels, sample_width)
        return CanonicalAudio(
            pcm16=frames,
            sample_rate_hz=sample_rate,
            duration_ms=duration_ms,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            canonical_sha256=hashlib.sha256(canonical_header + frames).hexdigest(),
        )
