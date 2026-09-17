"""OpenAI-compatible HTTP adapter for managed AI Gateway routing."""

from __future__ import annotations

import hmac
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import uvicorn
from databricks.sdk import WorkspaceClient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from voiceguard.adapters.databricks import DatabricksResponsesTranscriber
from voiceguard.adapters.databricks_semantic import DatabricksGatewaySemanticEvaluator
from voiceguard.adapters.lakebase import (
    LakebaseConnectionFactory,
    LakebaseDecisionStore,
)
from voiceguard.audio import AudioRejected, WavPcm16Normalizer
from voiceguard.config import Settings, TenantProfile
from voiceguard.contracts import Decision, EvaluationContext, Outcome
from voiceguard.engine import VoiceGuardEngine
from voiceguard.openai_profile import MODEL, parse_request
from voiceguard.policies import PaymentCardPolicy, RequiredSemanticPolicy
from voiceguard.readiness import Readiness


def create_app(
    settings: Settings,
    *,
    engine: VoiceGuardEngine | None = None,
    normalizer: WavPcm16Normalizer | None = None,
    readiness: Readiness | None = None,
) -> FastAPI:
    app = FastAPI(title="VoiceGuard", version="0.1.0", docs_url=None, redoc_url=None)
    if engine is None:
        client = WorkspaceClient()
        transcriber = DatabricksResponsesTranscriber(
            endpoint=settings.databricks_stt_endpoint,
            client=client,
        )
        evaluator = DatabricksGatewaySemanticEvaluator(
            model_service=settings.semantic_model_service,
            expected_destination=settings.semantic_model_version,
            client=client,
        )
        decision_store = LakebaseDecisionStore(
            LakebaseConnectionFactory(
                instance=settings.lakebase_instance,
                database=settings.lakebase_database,
                port=settings.lakebase_port,
                client=client,
            ),
            schema=settings.lakebase_schema,
        )
        decision_store.ensure_schema()
        active_engine = VoiceGuardEngine(
            transcriber=transcriber,
            policies=(
                PaymentCardPolicy(),
                RequiredSemanticPolicy(
                    evaluator,
                    allow_threshold=settings.semantic_allow_threshold,
                ),
            ),
            supported_languages=settings.supported_languages,
            policy_version=settings.policy_version,
            replay_store=decision_store,
            ledger=decision_store,
        )
        active_readiness = readiness or Readiness(
            {
                "lakebase": decision_store.ping,
                "evaluator": evaluator.ping,
                "stt": transcriber.ping,
            }
        )
    else:
        active_engine = engine
        active_readiness = readiness or Readiness({"test": lambda: None})
    active_normalizer = normalizer or WavPcm16Normalizer()

    @app.middleware("http")
    async def enforce_request_size(request: Request, call_next):
        if request.method == "POST":
            declared = request.headers.get("content-length")
            if declared is None or not declared.isdigit():
                return _error(411, "invalid_request_error", "Content-Length is required")
            if int(declared) > settings.max_request_bytes:
                return _error(413, "invalid_request_error", "request exceeds the size limit")
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "live",
            "schema_version": "voiceguard/decision/v1",
            "policy_version": settings.policy_version,
        }

    @app.get("/ready")
    async def ready() -> JSONResponse:
        failures = active_readiness.failures()
        if failures:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "failures": list(failures)},
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "schema_version": "voiceguard/decision/v1"},
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        tenant = _authenticate(request, settings)
        if tenant is None:
            return _error(401, "authentication_error", "invalid provider credential")
        media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type != "application/json":
            return _error(415, "invalid_request_error", "Content-Type must be application/json")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            return _error(
                415,
                "invalid_request_error",
                "compressed request bodies are not accepted",
            )
        try:
            raw_body = await request.body()
            declared_length = int(request.headers["content-length"])
            if len(raw_body) != declared_length or len(raw_body) > settings.max_request_bytes:
                raise ValueError("request body length does not match the enforced limit")
            metadata, encoded_audio = parse_request(raw_body)
            audio = active_normalizer.decode_base64(encoded_audio)
            context = EvaluationContext(
                tenant_id=tenant.tenant_id,
                request_id=metadata["request_id"],
                nonce=metadata["nonce"],
                policy_bundle=tenant.policy_bundle,
                enforced_languages=tenant.enforced_languages,
                requested_language=metadata.get("language"),
                received_at=datetime.now(UTC),
            )
        except (AudioRejected, UnicodeDecodeError, ValueError) as exc:
            return _error(400, "invalid_request_error", str(exc))

        try:
            decision = active_engine.evaluate(context, audio)
        except Exception:
            return _error(503, "voiceguard_error", "required VoiceGuard component failed")
        if decision.outcome == Outcome.ALLOW:
            return JSONResponse(status_code=200, content=_completion(decision))
        status = {
            Outcome.DENY: 403,
            Outcome.REVIEW: 409,
            Outcome.ERROR: 503,
        }[decision.outcome]
        return _error(
            status,
            f"voiceguard_{decision.outcome.value.lower()}",
            decision.reason,
            decision=decision,
        )

    return app


def _authenticate(request: Request, settings: Settings) -> TenantProfile | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    supplied_hash = Settings.hash_api_key(authorization.removeprefix("Bearer "))
    matched: TenantProfile | None = None
    for tenant in settings.tenants:
        for expected_hash in tenant.api_key_sha256s:
            if hmac.compare_digest(supplied_hash, expected_hash):
                matched = tenant
    return matched


def _completion(decision: Decision) -> dict[str, Any]:
    return {
        "id": decision.request_id,
        "object": "chat.completion",
        "created": round(decision.issued_at.timestamp()),
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": decision.sanitized_transcript or "",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "voiceguard": _decision_payload(decision),
    }


def _decision_payload(decision: Decision) -> dict[str, Any]:
    payload = asdict(decision)
    payload["outcome"] = decision.outcome.value
    payload["reason_code"] = decision.reason_code.value
    payload["issued_at"] = decision.issued_at.isoformat()
    payload["expires_at"] = decision.expires_at.isoformat()
    payload["findings"] = [
        {
            **asdict(finding),
            "outcome": finding.outcome.value,
        }
        for finding in decision.findings
    ]
    payload.pop("sanitized_transcript", None)
    return payload


def _error(
    status: int,
    error_type: str,
    message: str,
    *,
    decision: Decision | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "type": error_type,
            "message": message,
        }
    }
    if decision is not None:
        body["voiceguard"] = _decision_payload(decision)
    return JSONResponse(status_code=status, content=body)


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8080, proxy_headers=False)
