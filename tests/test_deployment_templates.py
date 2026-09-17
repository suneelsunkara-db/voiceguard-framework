import importlib.util
from pathlib import Path


def _reconcile_module():
    path = Path("deploy/databricks/reconcile.py")
    spec = importlib.util.spec_from_file_location("voiceguard_reconcile", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_service_has_no_fallback_or_raw_payload_table() -> None:
    template = Path("deploy/databricks/model-service.json.tmpl").read_text()
    assert '"fallback"' not in template
    assert '"inference_table"' not in template
    assert "DESTINATION_TYPE_EXTERNAL_FOUNDATION_MODEL" in template


def test_provider_uses_exact_managed_chat_origin_route() -> None:
    template = Path("deploy/databricks/model-provider-service.json.tmpl").read_text()
    assert "${VOICEGUARD_ORIGIN}/v1/chat/completions" in template
    assert "openai/v1/chat/completions" in template
    assert '"forward_unmanaged_paths": false' in template
    assert '"allow_all_targets": false' in template


def test_reconciler_builds_closed_route() -> None:
    module = _reconcile_module()
    provider = module.provider_body("https://voiceguard.example", "x" * 32)
    config = provider["config"]
    assert config["custom"]["direct"]["base_url"] == (
        "https://voiceguard.example/v1/chat/completions"
    )
    assert config["forward_unmanaged_paths"] is False
    assert config["allow_all_targets"] is False

    service = module.model_service_body("catalog.schema.provider", 60)
    route = service["config"]["routing"]["destinations"]
    assert len(route) == 1
    assert route[0]["traffic_percentage"] == 100
    assert "fallback" not in service["config"]
    assert "inference_table" not in service["config"]
