"""Environment configuration with startup-time validation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TenantProfile:
    tenant_id: str
    api_key_sha256s: tuple[str, ...]
    policy_bundle: str
    enforced_languages: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    tenants: tuple[TenantProfile, ...]
    databricks_stt_endpoint: str
    supported_languages: tuple[str, ...]
    policy_version: str
    semantic_model_service: str
    semantic_model_version: str
    semantic_allow_threshold: float
    lakebase_instance: str
    lakebase_database: str
    lakebase_port: int
    lakebase_schema: str
    max_request_bytes: int
    hosting_target: str = "independent-https"
    provider_auth: str = "rotatable-api-key"

    @classmethod
    def from_env(cls) -> Settings:
        app_config = _app_config()
        languages = tuple(
            item.strip()
            for item in _required("VOICEGUARD_SUPPORTED_LANGUAGES").split(",")
            if item.strip()
        )
        tenants = _tenant_profiles(languages)
        threshold = float(os.getenv("VOICEGUARD_SEMANTIC_ALLOW_THRESHOLD", "0.99"))
        if not 0 < threshold <= 1:
            raise ValueError("VOICEGUARD_SEMANTIC_ALLOW_THRESHOLD must be in (0, 1]")
        hosting_target = os.getenv("VOICEGUARD_HOSTING_TARGET", "independent-https").strip()
        if hosting_target != "independent-https":
            raise ValueError(
                "VoiceGuard origin must be an independent HTTPS runtime; "
                "Databricks Apps are not a permitted hosting target"
            )
        provider_auth = os.getenv("VOICEGUARD_PROVIDER_AUTH", "rotatable-api-key").strip()
        if provider_auth != "rotatable-api-key":
            raise ValueError(
                "provider auth must be a rotatable API key; "
                "Databricks App OAuth cannot be stored as the Gateway credential"
            )
        stt_endpoint = os.getenv("VOICEGUARD_DATABRICKS_STT_ENDPOINT", "").strip()
        if not stt_endpoint:
            candidates = _nested(app_config, "realtime_voice", "stt_candidates") or {}
            stt_endpoint = next(
                (
                    str(item.get("endpoint") or "")
                    for item in candidates.values()
                    if isinstance(item, dict) and item.get("endpoint")
                ),
                "",
            )
        qwen = _nested(app_config, "ai_gateway", "model_services", "qwen") or {}
        return cls(
            tenants=tenants,
            databricks_stt_endpoint=stt_endpoint or _required("VOICEGUARD_DATABRICKS_STT_ENDPOINT"),
            supported_languages=languages,
            policy_version=_required("VOICEGUARD_POLICY_VERSION"),
            semantic_model_service=(
                os.getenv("VOICEGUARD_SEMANTIC_MODEL_SERVICE", "").strip()
                or str(qwen.get("service") or "")
                or _required("VOICEGUARD_SEMANTIC_MODEL_SERVICE")
            ),
            semantic_model_version=(
                os.getenv("VOICEGUARD_SEMANTIC_MODEL_VERSION", "").strip()
                or str(qwen.get("destination") or "")
                or _required("VOICEGUARD_SEMANTIC_MODEL_VERSION")
            ),
            semantic_allow_threshold=threshold,
            lakebase_instance=(
                os.getenv("VOICEGUARD_LAKEBASE_INSTANCE", "").strip()
                or str(_nested(app_config, "lakebase", "instance") or "")
                or _required("VOICEGUARD_LAKEBASE_INSTANCE")
            ),
            lakebase_database=(
                os.getenv("VOICEGUARD_LAKEBASE_DATABASE", "").strip()
                or str(_nested(app_config, "lakebase", "database") or "")
                or _required("VOICEGUARD_LAKEBASE_DATABASE")
            ),
            lakebase_port=int(
                os.getenv("VOICEGUARD_LAKEBASE_PORT", "")
                or _nested(app_config, "lakebase", "port")
                or 5432
            ),
            lakebase_schema=(
                os.getenv("VOICEGUARD_LAKEBASE_SCHEMA", "").strip()
                or str(_nested(app_config, "lakebase", "schema") or "")
                or _required("VOICEGUARD_LAKEBASE_SCHEMA")
            ),
            max_request_bytes=int(os.getenv("VOICEGUARD_MAX_REQUEST_BYTES", "4000000")),
            hosting_target=hosting_target,
            provider_auth=provider_auth,
        )

    @staticmethod
    def hash_api_key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tenant_profiles(release_languages: tuple[str, ...]) -> tuple[TenantProfile, ...]:
    raw = json.loads(_required("VOICEGUARD_TENANTS"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("VOICEGUARD_TENANTS must be a non-empty JSON object")
    approved = {item.split("-", 1)[0].lower() for item in release_languages}
    profiles: list[TenantProfile] = []
    assigned_digests: set[str] = set()
    for tenant_id, value in raw.items():
        if not isinstance(tenant_id, str) or not tenant_id or not isinstance(value, dict):
            raise ValueError("each tenant profile must be an object")
        if set(value) != {"api_key_sha256s", "policy_bundle", "languages"}:
            raise ValueError("tenant profile fields are invalid")
        digests = value["api_key_sha256s"]
        policy_bundle = value["policy_bundle"]
        tenant_languages = value["languages"]
        if (
            not isinstance(digests, list)
            or not digests
            or any(not isinstance(item, str) or len(item) != 64 for item in digests)
        ):
            raise ValueError("tenant API keys must be a non-empty list of SHA-256 hex digests")
        for digest in digests:
            int(digest, 16)
            normalized_digest = digest.lower()
            if normalized_digest in assigned_digests:
                raise ValueError("tenant API-key hashes must be globally unique")
            assigned_digests.add(normalized_digest)
        if not isinstance(policy_bundle, str) or not policy_bundle:
            raise ValueError("tenant policy_bundle is required")
        if (
            not isinstance(tenant_languages, list)
            or not tenant_languages
            or any(not isinstance(item, str) for item in tenant_languages)
        ):
            raise ValueError("tenant languages must be a non-empty string list")
        if not {item.split("-", 1)[0].lower() for item in tenant_languages} <= approved:
            raise ValueError("tenant languages must be a subset of release-approved languages")
        profiles.append(
            TenantProfile(
                tenant_id=tenant_id,
                api_key_sha256s=tuple(digest.lower() for digest in digests),
                policy_bundle=policy_bundle,
                enforced_languages=tuple(tenant_languages),
            )
        )
    return tuple(profiles)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _app_config() -> dict[str, Any]:
    path = os.getenv("VOICEGUARD_APP_CONFIG", "").strip()
    if not path:
        return {}
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("VOICEGUARD_APP_CONFIG must contain a YAML object")
    return payload


def _nested(payload: dict[str, Any], *path: str) -> Any:
    value: Any = payload
    for item in path:
        if not isinstance(value, dict):
            return None
        value = value.get(item)
    return value
