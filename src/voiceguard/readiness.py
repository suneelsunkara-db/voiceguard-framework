"""Fail-closed dependency probes. Ready never implies transcript ALLOW."""

from __future__ import annotations

from collections.abc import Callable


class Readiness:
    def __init__(self, probes: dict[str, Callable[[], None]]) -> None:
        if not probes:
            raise ValueError("readiness requires at least one dependency probe")
        self.probes = probes

    def failures(self) -> tuple[str, ...]:
        failed: list[str] = []
        for name, probe in self.probes.items():
            try:
                probe()
            except Exception as exc:
                failed.append(f"{name}:{type(exc).__name__}")
        return tuple(failed)
