#!/usr/bin/env python3
"""Run the fail-closed VoiceGuard matrix through Unity AI Gateway."""

from __future__ import annotations

import argparse
import base64
import json
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from databricks.sdk import WorkspaceClient


def _audio_data(path: Path) -> str:
    with wave.open(str(path), "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getcomptype() != "NONE"
        ):
            raise ValueError(f"{path} must be mono uncompressed PCM16 WAV")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _body(
    *,
    model_service: str,
    audio_b64: str,
    request_id: str,
    nonce: str,
    language: str,
    stream: bool = False,
) -> dict[str, Any]:
    return {
        "model": model_service,
        "stream": stream,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "request_id": request_id,
                                "nonce": nonce,
                                "language": language,
                            },
                            separators=(",", ":"),
                        ),
                    },
                    {
                        "type": "audio_url",
                        "audio_url": {
                            "url": f"data:audio/wav;base64,{audio_b64}",
                        },
                    },
                ],
            }
        ],
    }


def _invoke(
    client: WorkspaceClient,
    *,
    model_service: str,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    response = requests.post(
        f"{client.config.host.rstrip('/')}/ai-gateway/mlflow/v1/chat/completions",
        headers={
            **dict(client.config.authenticate() or {}),
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"error": {"type": "non_json_gateway_response"}}
    return response.status_code, payload


def _assert_result(
    *,
    name: str,
    status: int,
    payload: dict[str, Any],
    expected_status: int,
    expected_outcome: str | None,
) -> dict[str, Any]:
    if status != expected_status:
        raise RuntimeError(f"{name}: expected HTTP {expected_status}, got {status}")
    decision = payload.get("voiceguard") or {}
    outcome = decision.get("outcome")
    if expected_outcome and outcome != expected_outcome:
        raise RuntimeError(f"{name}: expected {expected_outcome}, got {outcome!r}")
    if status != 200 and (
        "choices" in payload
        or "sanitized_transcript" in payload
        or "sanitized_transcript" in decision
    ):
        raise RuntimeError(f"{name}: denied response exposed transcript-shaped content")
    if status == 200:
        choices = payload.get("choices") or []
        if outcome != "ALLOW" or not choices:
            raise RuntimeError(f"{name}: HTTP 200 was not a complete ALLOW")
    return {"name": name, "status": status, "outcome": outcome or "TRANSPORT_REJECT"}


def _ids(label: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex
    return f"vgc_{label}_{suffix}", f"nonce_{label}_{suffix}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("--model-service", required=True)
    parser.add_argument("--safe-wav", type=Path, required=True)
    parser.add_argument("--card-wav", type=Path)
    parser.add_argument(
        "--evaluator-down",
        action="store_true",
        help="Run only the 503 probe while the certified evaluator is intentionally unavailable",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    client = WorkspaceClient(profile=args.profile or None)
    safe_audio = _audio_data(args.safe_wav)
    results: list[dict[str, Any]] = []

    if args.evaluator_down:
        request_id, nonce = _ids("evaluator_down")
        status, payload = _invoke(
            client,
            model_service=args.model_service,
            body=_body(
                model_service=args.model_service,
                audio_b64=safe_audio,
                request_id=request_id,
                nonce=nonce,
                language="en-US",
            ),
        )
        results.append(
            _assert_result(
                name="evaluator-down",
                status=status,
                payload=payload,
                expected_status=503,
                expected_outcome="ERROR",
            )
        )
    else:
        request_id, nonce = _ids("safe")
        safe_body = _body(
            model_service=args.model_service,
            audio_b64=safe_audio,
            request_id=request_id,
            nonce=nonce,
            language="en-US",
        )
        status, payload = _invoke(
            client,
            model_service=args.model_service,
            body=safe_body,
        )
        results.append(
            _assert_result(
                name="english-allow",
                status=status,
                payload=payload,
                expected_status=200,
                expected_outcome="ALLOW",
            )
        )

        status, payload = _invoke(
            client,
            model_service=args.model_service,
            body=safe_body,
        )
        results.append(
            _assert_result(
                name="replay-deny",
                status=status,
                payload=payload,
                expected_status=403,
                expected_outcome="DENY",
            )
        )

        stream_id, stream_nonce = _ids("stream")
        status, payload = _invoke(
            client,
            model_service=args.model_service,
            body=_body(
                model_service=args.model_service,
                audio_b64=safe_audio,
                request_id=stream_id,
                nonce=stream_nonce,
                language="en-US",
                stream=True,
            ),
        )
        results.append(
            _assert_result(
                name="stream-reject",
                status=status,
                payload=payload,
                expected_status=400,
                expected_outcome=None,
            )
        )

        language_id, language_nonce = _ids("unknown_language")
        status, payload = _invoke(
            client,
            model_service=args.model_service,
            body=_body(
                model_service=args.model_service,
                audio_b64=safe_audio,
                request_id=language_id,
                nonce=language_nonce,
                language="zz-ZZ",
            ),
        )
        results.append(
            _assert_result(
                name="unknown-language-deny",
                status=status,
                payload=payload,
                expected_status=403,
                expected_outcome="DENY",
            )
        )

        if args.card_wav:
            card_id, card_nonce = _ids("card")
            status, payload = _invoke(
                client,
                model_service=args.model_service,
                body=_body(
                    model_service=args.model_service,
                    audio_b64=_audio_data(args.card_wav),
                    request_id=card_id,
                    nonce=card_nonce,
                    language="en-US",
                ),
            )
            results.append(
                _assert_result(
                    name="payment-card-deny",
                    status=status,
                    payload=payload,
                    expected_status=403,
                    expected_outcome="DENY",
                )
            )

    report = {
        "schema_version": "voiceguard/canary-report/v1",
        "model_service": args.model_service,
        "executed_at": datetime.now(UTC).isoformat(),
        "results": results,
        "passed": True,
    }
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
