from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from voiceguard_core.contracts import (
    CanonicalAudio,
    Decision,
    Outcome,
    ReasonCode,
    Transcript,
)

from voiceguard.adapters.databricks import DatabricksResponsesTranscriber
from voiceguard.adapters.databricks_semantic import DatabricksGatewaySemanticEvaluator
from voiceguard.adapters.lakebase import LakebaseDecisionStore


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.payload)


class ApiClient:
    def __init__(self):
        self.calls = []

    def do(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {
            "config": {
                "routing": {
                    "destinations": [
                        {"pay_per_token_config": {"model": "models/system.ai.qwen3-next"}}
                    ]
                }
            }
        }


class Cursor:
    def __init__(self):
        self.calls = []
        self.row = ("nonce_123456",)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, statement, parameters=None):
        self.calls.append((statement, parameters))

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return self._cursor


class ConnectionFactory:
    def __init__(self):
        self.cursor = Cursor()

    def connect(self):
        return Connection(self.cursor)


def test_databricks_transcriber_refreshes_auth_and_ignores_language_hint() -> None:
    auth_calls = []
    config = SimpleNamespace(
        host="https://workspace.example",
        authenticate=lambda: auth_calls.append(True) or {"Authorization": "Bearer oauth"},
    )
    client = SimpleNamespace(config=config)
    session = Session(
        {
            "model": "stt-v1",
            "custom_outputs": {
                "transcript": "hola",
                "detected_language": "es",
                "confidence": 0.99,
            },
        }
    )
    adapter = DatabricksResponsesTranscriber(
        endpoint="stt",
        client=client,
        session=session,
    )
    result = adapter.transcribe(
        CanonicalAudio(b"\0\0" * 1600, 16_000, 100, "a" * 64, "b" * 64),
        requested_language="en-US",
        request_id="request_123456",
    )
    assert result.detected_language == "es"
    assert len(auth_calls) == 1
    assert session.calls[0][1]["json"]["custom_inputs"]["language"] is None


def test_semantic_adapter_requires_exact_shape_and_model_version() -> None:
    valid = {
        "schema_version": "voiceguard/semantic-assessment/v1",
        "request_id": "request_123456",
        "policy_bundle": "strict-v1",
        "text_sha256": hashlib.sha256(b"hola").hexdigest(),
        "outcome": "SAFE",
        "confidence": 0.999,
        "categories": [],
    }
    session = Session({"choices": [{"message": {"content": json.dumps(valid)}}]})
    client = SimpleNamespace(
        config=SimpleNamespace(
            host="https://workspace.example",
            authenticate=lambda: {"Authorization": "Bearer oauth"},
        ),
        api_client=ApiClient(),
    )
    adapter = DatabricksGatewaySemanticEvaluator(
        model_service="catalog.schema.semantic",
        expected_destination="system.ai.qwen3-next",
        client=client,
        session=session,
    )
    assessment = adapter.assess(
        transcript=Transcript("hola", "es", 0.99, "stt-v1"),
        policy_bundle="strict-v1",
        tenant_id="tenant-a",
        request_id="request_123456",
    )
    assert assessment.safe

    invalid = {**valid, "unexpected": True}
    adapter.session = Session({"choices": [{"message": {"content": json.dumps(invalid)}}]})
    with pytest.raises(ValueError, match="unknown or missing"):
        adapter.assess(
            transcript=Transcript("hola", "es", 0.99, "stt-v1"),
            policy_bundle="strict-v1",
            tenant_id="tenant-a",
            request_id="request_123456",
        )


def test_lakebase_store_uses_atomic_nonce_and_metadata_only_decisions() -> None:
    factory = ConnectionFactory()
    store = LakebaseDecisionStore(factory, schema="voiceguard")
    claimed = store.claim(
        tenant_id="tenant-a",
        nonce="nonce_123456",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    assert claimed
    nonce_sql, nonce_parameters = factory.cursor.calls[-1]
    assert "ON CONFLICT (tenant_id, nonce) DO NOTHING" in nonce_sql
    assert nonce_parameters[0:2] == ("tenant-a", "nonce_123456")

    now = datetime.now(UTC)
    store.record(
        Decision(
            schema_version="voiceguard/decision/v1",
            outcome=Outcome.ALLOW,
            reason_code=ReasonCode.SAFE,
            reason="safe",
            tenant_id="tenant-a",
            request_id="request_123456",
            nonce="nonce_123456",
            policy_bundle="strict-v1",
            policy_version="policy-v1",
            source_audio_sha256="a" * 64,
            canonical_audio_sha256="b" * 64,
            transcript_sha256="c" * 64,
            detected_language="en",
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
            sanitized_transcript="secret transcript",
        )
    )
    decision_sql, decision_parameters = factory.cursor.calls[-1]
    assert "voiceguard_decisions" in decision_sql
    assert "secret transcript" not in decision_parameters
    assert "c" * 64 in decision_parameters
