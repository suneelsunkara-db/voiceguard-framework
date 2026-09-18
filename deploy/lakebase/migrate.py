#!/usr/bin/env python3
"""Create VoiceGuard Lakebase tables and grant a runtime service principal DML."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml
from databricks.sdk import WorkspaceClient

from voiceguard.adapters.lakebase import (
    LakebaseConnectionFactory,
    LakebaseDecisionStore,
)
from voiceguard.config import Settings

_PRINCIPAL = re.compile(r"[0-9a-fA-F-]{36}")


def _settings(path: Path) -> Settings:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    languages = (raw.get("release") or {}).get("supported_languages") or []
    if not languages:
        raise ValueError("release.supported_languages is required")
    dummy_tenants = json.dumps(
        {
            "migration-validation": {
                "api_key_sha256s": [hashlib.sha256(b"migration-only").hexdigest()],
                "policy_bundle": "migration-only",
                "languages": [languages[0]],
            }
        }
    )
    return Settings.from_file(path, tenants_json=dummy_tenants)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/voiceguard.yaml"))
    parser.add_argument("--profile")
    parser.add_argument("--runtime-principal", required=True)
    args = parser.parse_args()
    principal = args.runtime_principal.strip()
    if not _PRINCIPAL.fullmatch(principal):
        raise SystemExit("--runtime-principal must be a service-principal UUID")

    settings = _settings(args.config)
    client = WorkspaceClient(profile=args.profile or None)
    factory = LakebaseConnectionFactory(
        instance=settings.lakebase_instance,
        database=settings.lakebase_database,
        port=settings.lakebase_port,
        client=client,
    )
    store = LakebaseDecisionStore(factory, schema=settings.lakebase_schema)
    store.ensure_schema()

    schema = '"' + settings.lakebase_schema.replace('"', '""') + '"'
    database = '"' + settings.lakebase_database.replace('"', '""') + '"'
    role = '"' + principal + '"'
    with factory.connect() as connection, connection.cursor() as cursor:
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS databricks_auth")
            cursor.execute(
                "SELECT databricks_create_role(%s, 'SERVICE_PRINCIPAL')",
                (principal,),
            )
        except Exception:
            # Existing role is expected on repeat runs; grants below remain mandatory.
            pass
        for statement in (
            f"GRANT CONNECT ON DATABASE {database} TO {role}",
            f"GRANT USAGE ON SCHEMA {schema} TO {role}",
            f"GRANT SELECT, INSERT ON {schema}.voiceguard_nonces TO {role}",
            f"GRANT SELECT, INSERT ON {schema}.voiceguard_decisions TO {role}",
        ):
            cursor.execute(statement)
    store.validate_schema()
    print(f"[voiceguard] Lakebase migration ready for {principal}")


if __name__ == "__main__":
    main()
