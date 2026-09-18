"""Dependency-free VoiceGuard client contracts."""

from voiceguard_client.contracts import (
    Admission,
    VoiceGuardRejected,
    build_request,
    parse_response,
)

__all__ = ["Admission", "VoiceGuardRejected", "build_request", "parse_response"]
