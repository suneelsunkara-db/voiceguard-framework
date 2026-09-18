from __future__ import annotations

import tomllib
from pathlib import Path


def _project(path: str) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))["project"]


def test_core_and_client_have_no_runtime_dependencies() -> None:
    core = _project("packages/voiceguard-core/pyproject.toml")
    client = _project("packages/voiceguard-client/pyproject.toml")
    assert core["name"] == "voiceguard-core"
    assert core["dependencies"] == []
    assert client["name"] == "voiceguard-client"
    assert client["dependencies"] == []


def test_server_depends_on_version_matched_core_only() -> None:
    server = _project("pyproject.toml")
    assert server["name"] == "voiceguard-server"
    assert "voiceguard-core==0.1.1" in server["dependencies"]
    assert all("voiceguard-client" not in item for item in server["dependencies"])
