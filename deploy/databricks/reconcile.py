#!/usr/bin/env python3
"""Reconcile the governed VoiceGuard route in Unity AI Gateway."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import urllib.request
from typing import Any

from databricks.sdk import WorkspaceClient

API = "/api/2.1/unity-catalog"
NATIVE_API = "openai/v1/chat/completions"
TARGET_MODEL = "voiceguard-v1"


def _api(
    client: WorkspaceClient,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["body"] = body
    return client.api_client.do(method, path, **kwargs)


def provider_body(origin: str, provider_key: str) -> dict[str, Any]:
    return {
        "comment": "VoiceGuard audio-native external provider",
        "config": {
            "provider_type": "EXTERNAL_MODEL_PROVIDER_TYPE_CUSTOM",
            "allow_all_targets": False,
            "targets": [
                {
                    "model": TARGET_MODEL,
                    "native_api_types": [NATIVE_API],
                }
            ],
            "forward_headers": False,
            "forward_query_parameters": False,
            "forward_unmanaged_paths": False,
            "custom": {
                "direct": {
                    "base_url": f"{origin.rstrip('/')}/v1/chat/completions",
                    "api_key": {"plaintext": provider_key},
                }
            },
        },
    }


def model_service_body(provider_fqn: str, requests_per_minute: int) -> dict[str, Any]:
    return {
        "comment": "Governed VoiceGuard route; no fallback or raw-payload table",
        "config": {
            "routing": {
                "destinations": [
                    {
                        "name": "voiceguard-primary",
                        "destination_type": "DESTINATION_TYPE_EXTERNAL_FOUNDATION_MODEL",
                        "traffic_percentage": 100,
                        "external_model_config": {
                            "model_provider_service": (f"model-provider-services/{provider_fqn}"),
                            "target": {
                                "model": TARGET_MODEL,
                                "native_api_types": [NATIVE_API],
                            },
                        },
                    }
                ]
            },
            "rate_limits": [
                {
                    "key": "RATE_LIMIT_KEY_SERVICE",
                    "renewal_period": "RATE_LIMIT_RENEWAL_PERIOD_MINUTE",
                    "requests": requests_per_minute,
                }
            ],
        },
    }


def _get_or_none(client: WorkspaceClient, path: str) -> dict[str, Any] | None:
    try:
        return _api(client, "GET", path)
    except Exception as exc:
        message = str(exc).lower()
        if "not found" in message or "does not exist" in message:
            return None
        raise


def _reconcile_provider(
    client: WorkspaceClient,
    *,
    catalog: str,
    schema: str,
    leaf: str,
    body: dict[str, Any],
) -> str:
    fqn = f"{catalog}.{schema}.{leaf}"
    path = f"{API}/model-provider-services/{fqn}"
    current = _get_or_none(client, path)
    if current is None:
        _api(
            client,
            "POST",
            (
                f"{API}/model-provider-services"
                f"?parent=schemas/{catalog}.{schema}&model_provider_service_id={leaf}"
            ),
            body,
        )
        print(f"[voiceguard] created provider: {fqn}")
    else:
        _api(
            client,
            "PATCH",
            f"{path}?update_mask=comment,config",
            {"name": f"model-provider-services/{fqn}", **body},
        )
        print(f"[voiceguard] updated provider: {fqn}")
    return fqn


def _reconcile_model_service(
    client: WorkspaceClient,
    *,
    catalog: str,
    schema: str,
    leaf: str,
    body: dict[str, Any],
) -> str:
    fqn = f"{catalog}.{schema}.{leaf}"
    path = f"{API}/model-services/{fqn}"
    current = _get_or_none(client, path)
    if current is None:
        _api(
            client,
            "POST",
            (f"{API}/model-services?parent=schemas/{catalog}.{schema}&model_service_id={leaf}"),
            body,
        )
        print(f"[voiceguard] created model service: {fqn}")
    else:
        _api(
            client,
            "PATCH",
            f"{path}?update_mask=comment,config.routing.destinations,config.rate_limits",
            {"name": f"model-services/{fqn}", **body},
        )
        print(f"[voiceguard] updated model service: {fqn}")
    return fqn


def _grant_model_service(
    client: WorkspaceClient,
    *,
    model_service_fqn: str,
    application_principal: str,
) -> None:
    _api(
        client,
        "PATCH",
        f"{API}/permissions/model_service/{model_service_fqn}",
        {"changes": [{"principal": application_principal, "add": ["EXECUTE"]}]},
    )
    print(
        "[voiceguard] granted Model Service EXECUTE: "
        f"{application_principal} -> {model_service_fqn}"
    )


def _assert_origin_ready(origin: str, ca_file: str | None) -> None:
    context = ssl.create_default_context(cafile=ca_file) if ca_file else None
    request = urllib.request.Request(
        f"{origin.rstrip('/')}/ready",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15, context=context) as response:
        payload = json.load(response)
    if response.status != 200 or payload.get("status") != "ready":
        raise RuntimeError(f"VoiceGuard origin is not ready: HTTP {response.status}")


def _assert_provider(current: dict[str, Any], origin: str) -> None:
    config = current.get("config") or {}
    if config.get("provider_type") != "EXTERNAL_MODEL_PROVIDER_TYPE_CUSTOM":
        raise RuntimeError("provider type drift")
    if config.get("allow_all_targets"):
        raise RuntimeError("provider unexpectedly allows all targets")
    if any(
        config.get(field)
        for field in (
            "forward_headers",
            "forward_query_parameters",
            "forward_unmanaged_paths",
        )
    ):
        raise RuntimeError("provider request forwarding must remain disabled")
    targets = config.get("targets") or []
    if len(targets) != 1 or targets[0].get("model") != TARGET_MODEL:
        raise RuntimeError("provider target allowlist drift")
    direct = (config.get("custom") or {}).get("direct") or {}
    expected = f"{origin.rstrip('/')}/v1/chat/completions"
    if direct.get("base_url") != expected:
        raise RuntimeError("provider base URL drift")


def _assert_model_service(
    current: dict[str, Any],
    *,
    provider_fqn: str,
    required_policy: str | None,
) -> None:
    config = current.get("config") or {}
    destinations = (config.get("routing") or {}).get("destinations") or []
    if len(destinations) != 1:
        raise RuntimeError("Model Service must have exactly one destination")
    destination = destinations[0]
    external = destination.get("external_model_config") or {}
    expected_provider = f"model-provider-services/{provider_fqn}"
    if external.get("model_provider_service") != expected_provider:
        raise RuntimeError("Model Service provider destination drift")
    if destination.get("traffic_percentage") != 100:
        raise RuntimeError("VoiceGuard destination must receive 100 percent traffic")
    if "fallback" in json.dumps(config).lower():
        raise RuntimeError("fallback is forbidden")
    if config.get("inference_table"):
        raise RuntimeError("raw-payload inference table is forbidden")
    if not config.get("rate_limits"):
        raise RuntimeError("Model Service rate limit is required")
    if required_policy:
        policies = json.dumps(config.get("service_policies") or {})
        if required_policy not in policies:
            raise RuntimeError(
                "required envelope policy is not attached; attach it in the "
                "Unity AI Gateway UI, then rerun reconcile"
            )


def _assert_no_provider_execute(
    client: WorkspaceClient,
    *,
    provider_fqn: str,
    application_principal: str,
) -> None:
    payload = _api(
        client,
        "GET",
        f"{API}/permissions/model_provider_service/{provider_fqn}",
    )
    for assignment in payload.get("privilege_assignments") or []:
        if assignment.get("principal") != application_principal:
            continue
        privileges = set(assignment.get("privileges") or [])
        if privileges & {"EXECUTE", "MANAGE", "ALL_PRIVILEGES"}:
            raise RuntimeError(
                f"application principal has forbidden provider access: {sorted(privileges)}"
            )


def _assert_no_serving_endpoint_access(
    client: WorkspaceClient,
    *,
    endpoint: str,
    application_principal: str,
) -> None:
    payload = client.api_client.do(
        "GET",
        f"/api/2.0/permissions/serving-endpoints/{endpoint}",
    )
    for assignment in payload.get("access_control_list") or []:
        principal = (
            assignment.get("service_principal_name")
            or assignment.get("user_name")
            or assignment.get("group_name")
        )
        if principal != application_principal:
            continue
        levels = {
            item.get("permission_level") for item in (assignment.get("all_permissions") or [])
        }
        if levels & {"CAN_QUERY", "CAN_MANAGE"}:
            raise RuntimeError(
                f"application principal can bypass VoiceGuard via {endpoint}: {sorted(levels)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--provider-service", required=True)
    parser.add_argument("--model-service", required=True)
    parser.add_argument("--application-principal", action="append", default=[])
    parser.add_argument(
        "--forbidden-serving-endpoint",
        action="append",
        default=[],
        help="Raw STT endpoint the application principal must not be able to query",
    )
    parser.add_argument("--requests-per-minute", type=int, default=60)
    parser.add_argument("--require-envelope-policy")
    parser.add_argument("--tls-ca-file")
    args = parser.parse_args()

    origin = os.environ.get("VOICEGUARD_ORIGIN", "").strip()
    provider_key = os.environ.get("VOICEGUARD_PROVIDER_API_KEY", "")
    if not origin.startswith("https://"):
        raise SystemExit("VOICEGUARD_ORIGIN must be an https:// URL")
    if len(provider_key) < 32:
        raise SystemExit("VOICEGUARD_PROVIDER_API_KEY must be at least 32 characters")
    if args.requests_per_minute < 1:
        raise SystemExit("--requests-per-minute must be positive")

    _assert_origin_ready(origin, args.tls_ca_file)
    client = WorkspaceClient(profile=args.profile or None)
    provider_fqn = _reconcile_provider(
        client,
        catalog=args.catalog,
        schema=args.schema,
        leaf=args.provider_service,
        body=provider_body(origin, provider_key),
    )
    model_service_fqn = _reconcile_model_service(
        client,
        catalog=args.catalog,
        schema=args.schema,
        leaf=args.model_service,
        body=model_service_body(provider_fqn, args.requests_per_minute),
    )
    for principal in args.application_principal:
        _grant_model_service(
            client,
            model_service_fqn=model_service_fqn,
            application_principal=principal,
        )

    provider = _api(client, "GET", f"{API}/model-provider-services/{provider_fqn}")
    service = _api(client, "GET", f"{API}/model-services/{model_service_fqn}")
    _assert_provider(provider, origin)
    _assert_model_service(
        service,
        provider_fqn=provider_fqn,
        required_policy=args.require_envelope_policy,
    )
    for principal in args.application_principal:
        _assert_no_provider_execute(
            client,
            provider_fqn=provider_fqn,
            application_principal=principal,
        )
        for endpoint in args.forbidden_serving_endpoint:
            _assert_no_serving_endpoint_access(
                client,
                endpoint=endpoint,
                application_principal=principal,
            )
    print(f"[voiceguard] governed route verified: {model_service_fqn}")


if __name__ == "__main__":
    main()
