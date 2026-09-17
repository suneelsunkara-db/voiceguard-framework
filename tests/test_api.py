from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from voiceguard.api import create_app
from voiceguard.config import Settings, TenantProfile
from voiceguard.contracts import CanonicalAudio, Decision, Outcome, ReasonCode
from voiceguard.readiness import Readiness


class Normalizer:
    def decode_base64(self, encoded):
        assert encoded == "AAAA"
        return CanonicalAudio(b"\0\0", 16_000, 100, "a" * 64, "b" * 64)


class Engine:
    def __init__(self, outcome=Outcome.ALLOW, *, fail=False):
        self.outcome = outcome
        self.fail = fail
        self.contexts = []

    def evaluate(self, context, audio):
        del audio
        if self.fail:
            raise TimeoutError("ledger unavailable")
        self.contexts.append(context)
        now = datetime.now(UTC)
        return Decision(
            schema_version="voiceguard/decision/v1",
            outcome=self.outcome,
            reason_code=(
                ReasonCode.SAFE if self.outcome == Outcome.ALLOW else ReasonCode.POLICY_VIOLATION
            ),
            reason="evaluated",
            tenant_id=context.tenant_id,
            request_id=context.request_id,
            nonce=context.nonce,
            policy_bundle=context.policy_bundle,
            policy_version="policy-v1",
            source_audio_sha256="a" * 64,
            canonical_audio_sha256="b" * 64,
            transcript_sha256="c" * 64,
            detected_language="en",
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
            sanitized_transcript="hello" if self.outcome == Outcome.ALLOW else None,
        )


def settings() -> Settings:
    return Settings(
        tenants=(
            TenantProfile(
                tenant_id="tenant-a",
                api_key_sha256s=(hashlib.sha256(b"provider-secret").hexdigest(),),
                policy_bundle="strict-v1",
                enforced_languages=("en",),
            ),
        ),
        databricks_stt_endpoint="stt",
        supported_languages=("en",),
        policy_version="policy-v1",
        semantic_model_service="catalog.schema.semantic",
        semantic_model_version="system.ai.qwen3-next",
        semantic_allow_threshold=0.99,
        lakebase_instance="genie_voice_lakebase",
        lakebase_database="databricks_postgres",
        lakebase_port=5432,
        lakebase_schema="genie_voice_contact_center",
        max_request_bytes=4_000_000,
    )


def request_body() -> dict:
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


def client(engine: Engine) -> TestClient:
    return TestClient(create_app(settings(), engine=engine, normalizer=Normalizer()))


def test_allow_returns_plain_transcript_and_structured_contract() -> None:
    engine = Engine()
    response = client(engine).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer provider-secret"},
        json=request_body(),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"]["content"] == "hello"
    assert payload["voiceguard"]["outcome"] == "ALLOW"
    assert not payload["choices"][0]["message"]["content"].startswith("VG1|")
    assert engine.contexts[0].tenant_id == "tenant-a"
    assert engine.contexts[0].policy_bundle == "strict-v1"


def test_deny_is_terminal_non_200_without_transcript() -> None:
    response = client(Engine(Outcome.DENY)).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer provider-secret"},
        json=request_body(),
    )
    assert response.status_code == 403
    assert "choices" not in response.json()
    assert response.json()["voiceguard"]["outcome"] == "DENY"


def test_review_and_error_are_terminal_without_transcript() -> None:
    for outcome, expected_status in (
        (Outcome.REVIEW, 409),
        (Outcome.ERROR, 503),
    ):
        response = client(Engine(outcome)).post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer provider-secret"},
            json=request_body(),
        )
        assert response.status_code == expected_status
        assert "choices" not in response.json()
        assert response.json()["voiceguard"]["outcome"] == outcome.value


def test_auth_and_runtime_fail_closed() -> None:
    unauthorized = client(Engine()).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong"},
        json=request_body(),
    )
    unavailable = client(Engine(fail=True)).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer provider-secret"},
        json=request_body(),
    )
    assert unauthorized.status_code == 401
    assert unavailable.status_code == 503
    assert "choices" not in unavailable.json()


def test_compressed_request_body_is_rejected() -> None:
    response = client(Engine()).post(
        "/v1/chat/completions",
        headers={
            "Authorization": "Bearer provider-secret",
            "Content-Encoding": "gzip",
        },
        json=request_body(),
    )
    assert response.status_code == 415


def test_readiness_fails_when_any_required_dependency_fails() -> None:
    def unavailable() -> None:
        raise TimeoutError("offline")

    app = create_app(
        settings(),
        engine=Engine(),
        normalizer=Normalizer(),
        readiness=Readiness({"lakebase": lambda: None, "evaluator": unavailable}),
    )
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "failures": ["evaluator:TimeoutError"],
    }
