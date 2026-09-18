"""Databricks adapters kept outside the framework core."""

from __future__ import annotations

import base64
import io
import wave
from typing import Any

import requests
from databricks.sdk import WorkspaceClient
from voiceguard_core.contracts import CanonicalAudio, Transcript


class DatabricksResponsesTranscriber:
    """Invoke an ``agent/v1/responses`` STT endpoint with refreshed OAuth auth."""

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float = 90,
        client: WorkspaceClient | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.client = client or WorkspaceClient()
        self.session = session or requests.Session()

    def transcribe(
        self,
        audio: CanonicalAudio,
        *,
        requested_language: str | None,
        request_id: str,
    ) -> Transcript:
        del requested_language
        wav_bytes = self._wav(audio)
        response = self.session.post(
            (
                f"{self.client.config.host.rstrip('/')}/serving-endpoints/"
                f"{self.endpoint}/invocations"
            ),
            headers={
                **dict(self.client.config.authenticate() or {}),
                "Content-Type": "application/json",
                "X-Request-Id": request_id,
            },
            json={
                "input": [{"role": "user", "content": "transcribe"}],
                "custom_inputs": {
                    "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                    "language": None,
                    "sample_rate_hz": audio.sample_rate_hz,
                },
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        text, language, confidence, version = self._extract(payload)
        if not text or not language or not version:
            raise RuntimeError("STT response omitted required transcript metadata")
        return Transcript(
            text=text,
            detected_language=language,
            confidence=confidence,
            model_version=version,
        )

    def ping(self) -> None:
        endpoint = self.client.serving_endpoints.get(self.endpoint)
        ready = str(getattr(getattr(endpoint, "state", None), "ready", ""))
        if ready.upper().split(".")[-1] != "READY":
            raise RuntimeError(f"STT endpoint is not ready: {ready or 'unknown'}")

    def _extract(self, payload: dict[str, Any]) -> tuple[str, str, float | None, str]:
        custom = payload.get("custom_outputs") or {}
        text = custom.get("transcript")
        if not text:
            for item in payload.get("output") or []:
                if not isinstance(item, dict):
                    continue
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                        text = part.get("text")
                        break
        language = custom.get("detected_language") or custom.get("language")
        confidence = custom.get("confidence")
        version = custom.get("model_version") or payload.get("model")
        return (
            str(text or "").strip(),
            str(language or "").strip(),
            float(confidence) if confidence is not None else None,
            str(version or self.endpoint),
        )

    @staticmethod
    def _wav(audio: CanonicalAudio) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(audio.sample_rate_hz)
            handle.writeframes(audio.pcm16)
        return buffer.getvalue()
