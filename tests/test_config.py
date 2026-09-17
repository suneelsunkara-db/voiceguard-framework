from __future__ import annotations

import hashlib
import json

import pytest

from voiceguard.config import Settings


def environment(monkeypatch, *, tenant_languages=("en",)):
    values = {
        "VOICEGUARD_TENANTS": json.dumps(
            {
                "tenant-a": {
                    "api_key_sha256s": [hashlib.sha256(b"secret").hexdigest()],
                    "policy_bundle": "strict-v1",
                    "languages": list(tenant_languages),
                }
            }
        ),
        "VOICEGUARD_DATABRICKS_STT_ENDPOINT": "stt",
        "VOICEGUARD_SUPPORTED_LANGUAGES": "en,es",
        "VOICEGUARD_POLICY_VERSION": "policy-v1",
        "VOICEGUARD_SEMANTIC_MODEL_SERVICE": "catalog.schema.semantic",
        "VOICEGUARD_SEMANTIC_MODEL_VERSION": "system.ai.qwen3-next",
        "VOICEGUARD_LAKEBASE_INSTANCE": "genie_voice_lakebase",
        "VOICEGUARD_LAKEBASE_DATABASE": "databricks_postgres",
        "VOICEGUARD_LAKEBASE_SCHEMA": "genie_voice_contact_center",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_tenant_policy_and_languages_are_server_side(monkeypatch) -> None:
    environment(monkeypatch)
    settings = Settings.from_env()
    assert settings.tenants[0].tenant_id == "tenant-a"
    assert settings.tenants[0].policy_bundle == "strict-v1"
    assert settings.tenants[0].enforced_languages == ("en",)


def test_resource_names_come_from_current_app_config(monkeypatch, tmp_path) -> None:
    environment(monkeypatch)
    for name in (
        "VOICEGUARD_DATABRICKS_STT_ENDPOINT",
        "VOICEGUARD_SEMANTIC_MODEL_SERVICE",
        "VOICEGUARD_SEMANTIC_MODEL_VERSION",
        "VOICEGUARD_LAKEBASE_INSTANCE",
        "VOICEGUARD_LAKEBASE_DATABASE",
        "VOICEGUARD_LAKEBASE_SCHEMA",
    ):
        monkeypatch.delenv(name)
    path = tmp_path / "config.yaml"
    path.write_text(
        """
ai_gateway:
  model_services:
    qwen:
      service: catalog.schema.qwen_guarded
      destination: system.ai.qwen3-next
lakebase:
  instance: genie_voice_lakebase
  database: databricks_postgres
  port: 5432
  schema: genie_voice_contact_center
realtime_voice:
  stt_candidates:
    qwen:
      endpoint: realtime_voice_stt_qwen
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOICEGUARD_APP_CONFIG", str(path))

    settings = Settings.from_env()

    assert settings.databricks_stt_endpoint == "realtime_voice_stt_qwen"
    assert settings.semantic_model_service == "catalog.schema.qwen_guarded"
    assert settings.semantic_model_version == "system.ai.qwen3-next"
    assert settings.lakebase_instance == "genie_voice_lakebase"
    assert settings.lakebase_schema == "genie_voice_contact_center"


def test_tenant_cannot_enable_uncertified_language(monkeypatch) -> None:
    environment(monkeypatch, tenant_languages=("zh",))
    with pytest.raises(ValueError, match="subset"):
        Settings.from_env()


def test_databricks_app_is_not_an_allowed_custom_provider_origin(monkeypatch) -> None:
    environment(monkeypatch)
    monkeypatch.setenv("VOICEGUARD_HOSTING_TARGET", "databricks-app")
    with pytest.raises(ValueError, match="independent HTTPS"):
        Settings.from_env()


def test_provider_auth_cannot_be_expiring_app_oauth(monkeypatch) -> None:
    environment(monkeypatch)
    monkeypatch.setenv("VOICEGUARD_PROVIDER_AUTH", "databricks-app-oauth")
    with pytest.raises(ValueError, match="rotatable API key"):
        Settings.from_env()


def test_provider_key_cannot_map_to_multiple_tenants(monkeypatch) -> None:
    environment(monkeypatch)
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
