#!/usr/bin/env python3
"""Validate that built VoiceGuard wheels preserve distribution boundaries."""

from __future__ import annotations

import argparse
import email
import zipfile
from pathlib import Path


def _metadata(path: Path) -> tuple[email.message.Message, set[str]]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError(f"{path.name} must contain exactly one METADATA file")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))
    return metadata, names


def validate(dist: Path) -> None:
    wheels = sorted(dist.glob("voiceguard_*-py3-none-any.whl"))
    if len(wheels) != 3:
        raise ValueError("dist must contain exactly three universal VoiceGuard wheels")
    by_name = {}
    contents = {}
    for wheel in wheels:
        metadata, names = _metadata(wheel)
        name = str(metadata["Name"])
        by_name[name] = metadata
        contents[name] = names

    expected = {"voiceguard-core", "voiceguard-client", "voiceguard-server"}
    if set(by_name) != expected:
        raise ValueError("wheel distribution names do not match the release contract")
    versions = {str(item["Version"]) for item in by_name.values()}
    if len(versions) != 1:
        raise ValueError("VoiceGuard wheel versions must match")
    version = versions.pop()

    for dependency_free in ("voiceguard-core", "voiceguard-client"):
        if by_name[dependency_free].get_all("Requires-Dist"):
            raise ValueError(f"{dependency_free} must have zero runtime dependencies")
    server_requirements = set(by_name["voiceguard-server"].get_all("Requires-Dist") or [])
    if f"voiceguard-core=={version}" not in server_requirements:
        raise ValueError("voiceguard-server must pin the matching voiceguard-core")
    if any(name.startswith("voiceguard_client/") for name in contents["voiceguard-server"]):
        raise ValueError("server wheel must not bundle the client")
    if any(name.startswith("voiceguard_core/") for name in contents["voiceguard-server"]):
        raise ValueError("server wheel must not bundle the core")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path)
    args = parser.parse_args()
    validate(args.dist)
    print("[voiceguard] wheel boundaries verified")


if __name__ == "__main__":
    main()
