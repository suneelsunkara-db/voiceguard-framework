"""VoiceGuard-owned deployment configuration with startup validation."""

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
    hosting_target: str
    provider_auth: str

    @classmethod
    def from_env(cls) -> Settings:
        path = Path(_required_env("VOICEGUARD_CONFIG"))
        return cls.from_file(path, tenants_json=_required_env("VOICEGUARD_TENANTS"))

    @classmethod
    def from_file(cls, path: Path, *, tenants_json: str) -> Settings:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        root = _object(payload, "configuration")
        _exact_keys(
            root,
            {"schema_version", "runtime", "databricks", "lakebase", "release", "server"},
            "configuration",
        )
        if root["schema_version"] != "voiceguard/config/v1":
            raise ValueError("schema_version must be voiceguard/config/v1")

        runtime = _object(root["runtime"], "runtime")
        _exact_keys(runtime, {"hosting_target", "provider_auth"}, "runtime")
        hosting_target = _string(runtime["hosting_target"], "runtime.hosting_target")
        provider_auth = _string(runtime["provider_auth"], "runtime.provider_auth")
        if hosting_target != "independent-https":
            raise ValueError("VoiceGuard must run on an independent HTTPS origin")
        if provider_auth != "rotatable-api-key":
            raise ValueError("VoiceGuard provider auth must be a rotatable API key")

        databricks = _object(root["databricks"], "databricks")
        _exact_keys(
            databricks,
            {"stt_endpoint", "semantic_model_service", "semantic_model_version"},
            "databricks",
        )
        semantic_service = _string(
            databricks["semantic_model_service"],
            "databricks.semantic_model_service",
        )
        if len(semantic_service.split(".")) != 3:
            raise ValueError("semantic_model_service must be a Unity Catalog FQN")
        semantic_version = _string(
            databricks["semantic_model_version"],
            "databricks.semantic_model_version",
        )
        if not semantic_version.startswith("system.ai."):
            raise ValueError("semantic_model_version must pin an explicit system.ai model")

        lakebase = _object(root["lakebase"], "lakebase")
        _exact_keys(lakebase, {"instance", "database", "port", "schema"}, "lakebase")
        lakebase_schema = _string(lakebase["schema"], "lakebase.schema")
        if not lakebase_schema.startswith("voiceguard"):
            raise ValueError("Lakebase schema must be VoiceGuard-owned")

        release = _object(root["release"], "release")
        _exact_keys(
            release,
            {"policy_version", "supported_languages", "semantic_allow_threshold"},
            "release",
        )
        languages_value = release["supported_languages"]
        if (
            not isinstance(languages_value, list)
            or not languages_value
            or any(not isinstance(item, str) or not item.strip() for item in languages_value)
        ):
            raise ValueError("release.supported_languages must be a non-empty string list")
        languages = tuple(item.strip() for item in languages_value)
        threshold = release["semantic_allow_threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("release.semantic_allow_threshold must be numeric")
        threshold = float(threshold)
        if not 0 < threshold <= 1:
            raise ValueError("release.semantic_allow_threshold must be in (0, 1]")

        server = _object(root["server"], "server")
        _exact_keys(server, {"max_request_bytes"}, "server")
        max_request_bytes = server["max_request_bytes"]
        if isinstance(max_request_bytes, bool) or not isinstance(max_request_bytes, int):
            raise ValueError("server.max_request_bytes must be an integer")
        if max_request_bytes < 1:
            raise ValueError("server.max_request_bytes must be positive")

        return cls(
            tenants=_tenant_profiles(tenants_json, languages),
            databricks_stt_endpoint=_string(databricks["stt_endpoint"], "databricks.stt_endpoint"),
            supported_languages=languages,
            policy_version=_string(release["policy_version"], "release.policy_version"),
            semantic_model_service=semantic_service,
            semantic_model_version=semantic_version,
            semantic_allow_threshold=threshold,
            lakebase_instance=_string(lakebase["instance"], "lakebase.instance"),
            lakebase_database=_string(lakebase["database"], "lakebase.database"),
            lakebase_port=_positive_int(lakebase["port"], "lakebase.port"),
            lakebase_schema=lakebase_schema,
            max_request_bytes=max_request_bytes,
            hosting_target=hosting_target,
            provider_auth=provider_auth,
        )

    @staticmethod
    def hash_api_key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tenant_profiles(
    tenants_json: str,
    release_languages: tuple[str, ...],
) -> tuple[TenantProfile, ...]:
    raw = json.loads(tenants_json)
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
            raise ValueError("tenant API keys must be SHA-256 hex digests")
        for digest in digests:
            int(digest, 16)
            normalized = digest.lower()
            if normalized in assigned_digests:
                raise ValueError("tenant API-key hashes must be globally unique")
            assigned_digests.add(normalized)
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
                api_key_sha256s=tuple(item.lower() for item in digests),
                policy_bundle=policy_bundle,
                enforced_languages=tuple(tenant_languages),
            )
        )
    return tuple(profiles)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], path: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{path} has unknown or missing fields")


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{path} must be a positive integer")
    return value
