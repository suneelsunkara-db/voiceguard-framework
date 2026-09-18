from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _module():
    path = Path("tools/preflight.py")
    spec = importlib.util.spec_from_file_location("voiceguard_preflight", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_runs_every_check_and_fails_closed() -> None:
    module = _module()
    called: list[str] = []

    def passes() -> None:
        called.append("passes")

    def fails() -> None:
        called.append("fails")
        raise TimeoutError("must not appear in report")

    report = module.evaluate_checks({"first": fails, "second": passes})

    assert called == ["fails", "passes"]
    assert report["passed"] is False
    assert report["checks"] == [
        {"name": "first", "status": "FAIL", "error": "TimeoutError"},
        {"name": "second", "status": "PASS"},
    ]
    assert "must not appear" not in str(report)


def test_preflight_binds_authenticated_m2m_identity() -> None:
    module = _module()
    client = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                application_id="runtime-client-id",
                user_name="runtime-client-id",
                id="internal-id",
            )
        )
    )

    module._m2m_identity(client, "runtime-client-id")
