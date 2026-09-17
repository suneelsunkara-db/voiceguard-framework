"""Strict, dependency-free OpenAI request profile parser."""

from __future__ import annotations

import json
import re
from typing import Any

MODEL = "voiceguard-v1"
_DATA_URI_PREFIX = "data:audio/wav;base64,"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{8,128}")


def parse_request(raw: bytes) -> tuple[dict[str, str], str]:
    body = strict_json(raw)
    if not isinstance(body, dict):
        raise ValueError("request must be a JSON object")
    if set(body) - {"model", "messages", "stream"}:
        raise ValueError("request contains unsupported fields")
    if body.get("model") != MODEL:
        raise ValueError(f"model must be {MODEL}")
    if body.get("stream") not in (None, False):
        raise ValueError("streaming is not supported; submit one completed utterance")
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        raise ValueError("exactly one user message is required")
    message = messages[0]
    if (
        not isinstance(message, dict)
        or set(message) != {"role", "content"}
        or message.get("role") != "user"
    ):
        raise ValueError("message must contain only role=user and content")
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 2:
        raise ValueError("content must contain exactly one metadata and one audio block")
    text_blocks = [
        item for item in content if isinstance(item, dict) and item.get("type") == "text"
    ]
    audio_blocks = [
        item for item in content if isinstance(item, dict) and item.get("type") == "audio_url"
    ]
    if len(text_blocks) != 1 or len(audio_blocks) != 1:
        raise ValueError("one text block and one audio_url block are required")
    if set(text_blocks[0]) != {"type", "text"}:
        raise ValueError("metadata block contains unsupported fields")
    if set(audio_blocks[0]) != {"type", "audio_url"}:
        raise ValueError("audio block contains unsupported fields")
    audio_url = audio_blocks[0]["audio_url"]
    if not isinstance(audio_url, dict) or set(audio_url) != {"url"}:
        raise ValueError("audio_url must contain only url")
    url = audio_url["url"]
    if not isinstance(url, str) or not url.startswith(_DATA_URI_PREFIX):
        raise ValueError("only inline base64 WAV data is accepted")
    metadata = _metadata(text_blocks[0]["text"])
    return metadata, url.removeprefix(_DATA_URI_PREFIX)


def strict_json(raw: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)


def _metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, str):
        raise ValueError("metadata must be a JSON string")
    parsed = strict_json(value.encode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be an object")
    required = {"request_id", "nonce"}
    if not required.issubset(parsed) or set(parsed) - (required | {"language"}):
        raise ValueError("metadata requires request_id and nonce")
    if any(not isinstance(item, str) for item in parsed.values()):
        raise ValueError("metadata values must be strings")
    if not _IDENTIFIER.fullmatch(parsed["request_id"]):
        raise ValueError("request_id is invalid")
    if not _IDENTIFIER.fullmatch(parsed["nonce"]):
        raise ValueError("nonce is invalid")
    language = parsed.get("language")
    if language and not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", language):
        raise ValueError("language is not a simple BCP-47 tag")
    return parsed
