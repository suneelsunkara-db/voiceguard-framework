"""Lakebase-backed replay and metadata decision storage."""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Any

from databricks.sdk import WorkspaceClient

from voiceguard.contracts import Decision

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid Lakebase identifier: {value!r}")
    return f'"{value}"'


class LakebaseConnectionFactory:
    """Mint short-lived Lakebase OAuth credentials using Databricks M2M."""

    def __init__(
        self,
        *,
        instance: str,
        database: str,
        port: int,
        client: WorkspaceClient | None = None,
    ) -> None:
        if not instance or not database:
            raise ValueError("Lakebase instance and database are required")
        self.instance = instance
        self.database = database
        self.port = port
        self.client = client or WorkspaceClient()
        self._cached: tuple[dict[str, Any], float] | None = None
        self._lock = threading.Lock()

    def connect(self):
        import psycopg

        credential = self._credential()
        return psycopg.connect(
            host=credential["host"],
            port=self.port,
            dbname=self.database,
            user=credential["user"],
            password=credential["password"],
            sslmode="require",
            autocommit=True,
        )

    def _credential(self) -> dict[str, Any]:
        with self._lock:
            if self._cached and self._cached[1] > time.time():
                return self._cached[0]
            endpoint, host = self._resolve_endpoint()
            credential = self.client.api_client.do(
                "POST",
                "/api/2.0/postgres/credentials",
                body={"endpoint": endpoint},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            user = self.client.current_user.me().user_name
            value = {
                "host": host,
                "user": user,
                "password": credential["token"],
            }
            self._cached = (value, time.time() + 50 * 60)
            return value

    def _resolve_endpoint(self) -> tuple[str, str]:
        api = self.client.api_client
        projects = api.do("GET", "/api/2.0/postgres/projects").get("projects") or []
        project = next(
            (
                item
                for item in projects
                if self.instance
                in {
                    item.get("project_id"),
                    (item.get("status") or {}).get("display_name"),
                }
                or item.get("project_id") == self.instance.replace("_", "-")
            ),
            None,
        )
        if not project:
            raise RuntimeError(f"Lakebase project not found: {self.instance}")
        project_id = project["project_id"]
        branches = (
            api.do("GET", f"/api/2.0/postgres/projects/{project_id}/branches").get("branches") or []
        )
        branch = next(
            (item for item in branches if (item.get("status") or {}).get("default")),
            branches[0] if branches else None,
        )
        if not branch:
            raise RuntimeError(f"Lakebase project has no branch: {self.instance}")
        branch_id = branch["branch_id"]
        endpoints = (
            api.do(
                "GET",
                (f"/api/2.0/postgres/projects/{project_id}/branches/{branch_id}/endpoints"),
            ).get("endpoints")
            or []
        )
        endpoint = next(
            (
                item
                for item in endpoints
                if (item.get("status") or {}).get("endpoint_type") == "ENDPOINT_TYPE_READ_WRITE"
            ),
            endpoints[0] if endpoints else None,
        )
        if not endpoint:
            raise RuntimeError(f"Lakebase branch has no endpoint: {branch_id}")
        host = ((endpoint.get("status") or {}).get("hosts") or {}).get("host")
        if not host:
            raise RuntimeError(f"Lakebase endpoint is not ready: {endpoint.get('name')}")
        return str(endpoint["name"]), str(host)


class LakebaseDecisionStore:
    """Atomic nonce claim and metadata-only ledger on the app's Lakebase."""

    def __init__(
        self,
        connection_factory: LakebaseConnectionFactory,
        *,
        schema: str,
    ) -> None:
        self.connection_factory = connection_factory
        self.schema = _identifier(schema)
        self.nonce_table = f"{self.schema}.voiceguard_nonces"
        self.decision_table = f"{self.schema}.voiceguard_decisions"

    def ensure_schema(self) -> None:
        with self.connection_factory.connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.nonce_table} (
                    tenant_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, nonce)
                )
                """
            )
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.decision_table} (
                    tenant_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    policy_bundle TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    source_audio_sha256 TEXT NOT NULL,
                    canonical_audio_sha256 TEXT NOT NULL,
                    transcript_sha256 TEXT NOT NULL,
                    detected_language TEXT,
                    finding_policy_ids TEXT[] NOT NULL,
                    issued_at TIMESTAMPTZ NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (tenant_id, request_id)
                )
                """
            )

    def claim(self, *, tenant_id: str, nonce: str, expires_at: datetime) -> bool:
        with self.connection_factory.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self.nonce_table} (tenant_id, nonce, expires_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (tenant_id, nonce) DO NOTHING
                RETURNING nonce
                """,
                (tenant_id, nonce, expires_at),
            )
            return cursor.fetchone() is not None

    def record(self, decision: Decision) -> None:
        with self.connection_factory.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {self.decision_table} (
                    tenant_id, request_id, nonce, outcome, reason_code,
                    policy_bundle, policy_version, source_audio_sha256,
                    canonical_audio_sha256, transcript_sha256, detected_language,
                    finding_policy_ids, issued_at, expires_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    decision.tenant_id,
                    decision.request_id,
                    decision.nonce,
                    decision.outcome.value,
                    decision.reason_code.value,
                    decision.policy_bundle,
                    decision.policy_version,
                    decision.source_audio_sha256,
                    decision.canonical_audio_sha256,
                    decision.transcript_sha256,
                    decision.detected_language,
                    [item.policy_id for item in decision.findings],
                    decision.issued_at,
                    decision.expires_at,
                ),
            )

    def ping(self) -> None:
        with self.connection_factory.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("Lakebase readiness query failed")
