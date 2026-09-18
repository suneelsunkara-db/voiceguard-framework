from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from voiceguard_client import VoiceGuardRejected, build_request, parse_response
from voiceguard_core.contracts import (
    CanonicalAudio,
    Decision,
    Outcome,
    ReasonCode,
)

from voiceguard.api import create_app
from voiceguard.config import Settings, TenantProfile


class ContractEngine:
    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome

    def evaluate(self, context, audio) -> Decision:
        now = datetime.now(UTC)
        return Decision(
            schema_version="voiceguard/decision/v1",
            outcome=self.outcome,
            reason_code=(
                ReasonCode.SAFE if self.outcome == Outcome.ALLOW else ReasonCode.POLICY_VIOLATION
            ),
            reason="contract fixture",
            tenant_id=context.tenant_id,
            request_id=context.request_id,
            nonce=context.nonce,
            policy_bundle=context.policy_bundle,
            policy_version="voiceguard-0.1.1",
            source_audio_sha256=audio.source_sha256,
            canonical_audio_sha256=audio.canonical_sha256,
            transcript_sha256="transcript-hash",
            detected_language="en",
            issued_at=now,
            expires_at=now + timedelta(seconds=60),
            sanitized_transcript="hello" if self.outcome == Outcome.ALLOW else None,
        )


class ContractNormalizer:
    def decode_base64(self, encoded: str) -> CanonicalAudio:
        assert encoded
        return CanonicalAudio(b"\0\0", 16_000, 50, "source-hash", "canonical-hash")


def _settings() -> Settings:
    return Settings(
        tenants=(
            TenantProfile(
                tenant_id="contract-tenant",
                api_key_sha256s=(hashlib.sha256(b"provider-secret").hexdigest(),),
                policy_bundle="strict-v1",
                enforced_languages=("en",),
            ),
        ),
        databricks_stt_endpoint="stt",
        supported_languages=("en",),
        policy_version="voiceguard-0.1.1",
        semantic_model_service="catalog.schema.semantic",
        semantic_model_version="system.ai.qwen",
        semantic_allow_threshold=0.99,
        lakebase_instance="voiceguard-lakebase",
        lakebase_database="postgres",
        lakebase_port=5432,
        lakebase_schema="voiceguard",
        max_request_bytes=4_000_000,
        hosting_target="independent-https",
        provider_auth="rotatable-api-key",
    )


def _gateway_request(request_id: str) -> dict:
    request = build_request(
        model_service="catalog.schema.voiceguard",
        pcm16=b"\0\0" * 800,
        sample_rate_hz=16_000,
        request_id=request_id,
        nonce=f"nonce_{request_id}",
        language="en-US",
    )
    # Managed AI Gateway selects the service externally, then invokes the
    # provider's allowlisted target model.
    request["model"] = "voiceguard-v1"
    return request


def test_gateway_to_provider_to_client_allow_contract() -> None:
    request_id = "request_contract_allow"
    app = create_app(
        _settings(),
        engine=ContractEngine(Outcome.ALLOW),
        normalizer=ContractNormalizer(),
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer provider-secret"},
        json=_gateway_request(request_id),
    )

    admission = parse_response(
        response.status_code,
        response.json(),
        expected_request_id=request_id,
    )
    assert admission.transcript == "hello"
    assert admission.policy_version == "voiceguard-0.1.1"


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [(Outcome.DENY, 403), (Outcome.REVIEW, 409), (Outcome.ERROR, 503)],
)
def test_gateway_contract_never_releases_non_allow(
    outcome: Outcome,
    expected_status: int,
) -> None:
    request_id = f"request_contract_{outcome.value.lower()}"
    app = create_app(
        _settings(),
        engine=ContractEngine(outcome),
        normalizer=ContractNormalizer(),
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer provider-secret"},
        json=_gateway_request(request_id),
    )

    assert response.status_code == expected_status
    assert "choices" not in response.json()
    with pytest.raises(VoiceGuardRejected):
        parse_response(
            response.status_code,
            response.json(),
            expected_request_id=request_id,
        )
