from __future__ import annotations

import json

import pytest
from voiceguard_core.openai_profile import parse_request, strict_json


def request() -> dict:
    return {
        "model": "voiceguard-v1",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "request_id": "request_123456",
                                "nonce": "nonce_123456",
                                "language": "en-US",
                            }
                        ),
                    },
                    {
                        "type": "audio_url",
                        "audio_url": {"url": "data:audio/wav;base64,AAAA"},
                    },
                ],
            }
        ],
    }


def test_parses_single_canonical_profile() -> None:
    metadata, audio = parse_request(json.dumps(request()).encode())
    assert metadata["language"] == "en-US"
    assert audio == "AAAA"


def test_rejects_duplicate_json_keys_at_any_depth() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        strict_json(b'{"model":"one","model":"two"}')
    body = request()
    body["messages"][0]["content"][0]["text"] = (
        '{"request_id":"request_123456","request_id":"request_654321","nonce":"nonce_123456"}'
    )
    with pytest.raises(ValueError, match="duplicate"):
        parse_request(json.dumps(body).encode())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update({"stream": True}),
        lambda body: body.update({"temperature": 0}),
        lambda body: body["messages"].append(body["messages"][0]),
        lambda body: body["messages"][0]["content"].append({"type": "text", "text": "{}"}),
        lambda body: body["messages"][0]["content"][1]["audio_url"].update(
            {"url": "https://example.com/audio.wav"}
        ),
    ],
)
def test_rejects_ambiguous_or_unmanaged_shapes(mutate) -> None:
    body = request()
    mutate(body)
    with pytest.raises(ValueError):
        parse_request(json.dumps(body).encode())
