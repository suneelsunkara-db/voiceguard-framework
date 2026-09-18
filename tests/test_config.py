from __future__ import annotations

import hashlib
import json

import pytest

from voiceguard.config import Settings


def environment(monkeypatch, tmp_path, *, tenant_languages=("en",), **overrides):
    config = {
        "schema_version": "voiceguard/config/v1",
        "runtime": {
            "hosting_target": "independent-https",
            "provider_auth": "rotatable-api-key",
        },
        "databricks": {
            "stt_endpoint": "realtime_voice_stt_qwen",
            "semantic_model_service": "catalog.schema.qwen_guarded",
            "semantic_model_version": "system.ai.qwen3-next",
        },
        "lakebase": {
            "instance": "genie_voice_lakebase",
            "database": "databricks_postgres",
            "port": 5432,
            "schema": "voiceguard",
        },
        "release": {
            "policy_version": "policy-v1",
            "supported_languages": ["en", "es"],
            "semantic_allow_threshold": 0.99,
        },
        "server": {"max_request_bytes": 4_000_000},
    }
    for section, values in overrides.items():
        config[section].update(values)
    path = tmp_path / "voiceguard.yaml"
    import yaml

    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("VOICEGUARD_CONFIG", str(path))
    monkeypatch.setenv(
        "VOICEGUARD_TENANTS",
        json.dumps(
            {
                "tenant-a": {
                    "api_key_sha256s": [hashlib.sha256(b"secret").hexdigest()],
                    "policy_bundle": "strict-v1",
                    "languages": list(tenant_languages),
                }
            }
        ),
    )
    return path


def test_configuration_is_voiceguard_owned(monkeypatch, tmp_path) -> None:
    environment(monkeypatch, tmp_path)
    settings = Settings.from_env()
    assert settings.databricks_stt_endpoint == "realtime_voice_stt_qwen"
    assert settings.semantic_model_service == "catalog.schema.qwen_guarded"
    assert settings.lakebase_instance == "genie_voice_lakebase"
    assert settings.lakebase_schema == "voiceguard"


def test_tenant_policy_and_languages_are_server_side(monkeypatch, tmp_path) -> None:
    environment(monkeypatch, tmp_path)
    settings = Settings.from_env()
    assert settings.tenants[0].tenant_id == "tenant-a"
    assert settings.tenants[0].policy_bundle == "strict-v1"
    assert settings.tenants[0].enforced_languages == ("en",)


def test_tenant_cannot_enable_uncertified_language(monkeypatch, tmp_path) -> None:
    environment(monkeypatch, tmp_path, tenant_languages=("zh",))
    with pytest.raises(ValueError, match="subset"):
        Settings.from_env()


def test_databricks_app_is_not_an_allowed_origin(monkeypatch, tmp_path) -> None:
    environment(
        monkeypatch,
        tmp_path,
        runtime={"hosting_target": "databricks-app"},
    )
    with pytest.raises(ValueError, match="independent HTTPS"):
        Settings.from_env()


def test_provider_auth_cannot_be_expiring_app_oauth(monkeypatch, tmp_path) -> None:
    environment(
        monkeypatch,
        tmp_path,
        runtime={"provider_auth": "databricks-app-oauth"},
    )
    with pytest.raises(ValueError, match="rotatable API key"):
        Settings.from_env()


def test_shared_application_lakebase_schema_is_rejected(monkeypatch, tmp_path) -> None:
    environment(
        monkeypatch,
        tmp_path,
        lakebase={"schema": "application_shared"},
    )
    with pytest.raises(ValueError, match="VoiceGuard-owned"):
        Settings.from_env()


def test_provider_key_cannot_map_to_multiple_tenants(monkeypatch, tmp_path) -> None:
    environment(monkeypatch, tmp_path)
    digest = hashlib.sha256(b"secret").hexdigest()
    monkeypatch.setenv(
        "VOICEGUARD_TENANTS",
        json.dumps(
            {
                "tenant-a": {
                    "api_key_sha256s": [digest],
                    "policy_bundle": "strict-v1",
                    "languages": ["en"],
                },
                "tenant-b": {
                    "api_key_sha256s": [digest],
                    "policy_bundle": "strict-v1",
                    "languages": ["en"],
                },
            }
        ),
    )
    with pytest.raises(ValueError, match="globally unique"):
        Settings.from_env()
