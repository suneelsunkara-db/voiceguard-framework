#!/usr/bin/env python3
"""Fail-closed production preflight for a VoiceGuard server deployment."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient

from voiceguard.adapters.databricks import DatabricksResponsesTranscriber
from voiceguard.adapters.databricks_semantic import (
    DatabricksGatewaySemanticEvaluator,
)
from voiceguard.adapters.lakebase import (
    LakebaseConnectionFactory,
    LakebaseDecisionStore,
)
from voiceguard.config import Settings


def evaluate_checks(checks: dict[str, Callable[[], None]]) -> dict[str, Any]:
    results: list[dict[str, str]] = []
    passed = True
    for name, check in checks.items():
        try:
            check()
        except Exception as exc:  # fail closed while keeping reports secret-free
            passed = False
            results.append({"name": name, "status": "FAIL", "error": type(exc).__name__})
        else:
            results.append({"name": name, "status": "PASS"})
    return {
        "schema_version": "voiceguard/preflight/v1",
        "executed_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "checks": results,
    }


def _release(settings: Settings) -> None:
    if settings.policy_version == "unreleased":
        raise RuntimeError("an unreleased policy cannot be deployed")


def _m2m_identity(client: WorkspaceClient, expected_client_id: str) -> None:
    current = client.current_user.me()
    identities = {
        str(value)
        for value in (
            getattr(current, "application_id", None),
            getattr(current, "user_name", None),
            getattr(current, "id", None),
        )
        if value
    }
    if expected_client_id not in identities:
        raise RuntimeError("authenticated identity is not the configured M2M principal")


def _checks(settings: Settings, client: WorkspaceClient) -> dict[str, Callable[[], None]]:
    expected_client_id = os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    if not expected_client_id or not client_secret:
        raise ValueError("Databricks M2M environment variables are required")

    transcriber = DatabricksResponsesTranscriber(
        endpoint=settings.databricks_stt_endpoint,
        client=client,
    )
    evaluator = DatabricksGatewaySemanticEvaluator(
        model_service=settings.semantic_model_service,
        expected_destination=settings.semantic_model_version,
        client=client,
    )
    connection_factory = LakebaseConnectionFactory(
        instance=settings.lakebase_instance,
        database=settings.lakebase_database,
        port=settings.lakebase_port,
        client=client,
    )
    decision_store = LakebaseDecisionStore(
        connection_factory,
        schema=settings.lakebase_schema,
    )
    return {
        "release": lambda: _release(settings),
        "m2m_identity": lambda: _m2m_identity(client, expected_client_id),
        "stt_endpoint": transcriber.ping,
        "semantic_model_service": evaluator.ping,
        "lakebase_schema": decision_store.validate_schema,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    os.environ["VOICEGUARD_CONFIG"] = str(args.config)
    settings = Settings.from_env()
    client = WorkspaceClient()
    report = evaluate_checks(_checks(settings, client))
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
