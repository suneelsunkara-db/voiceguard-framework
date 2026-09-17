# ADR 0001: VoiceGuard owns semantic enforcement

Status: Accepted
Date: 15 September 2026

## Context

Unity AI Gateway can transport audio content to a custom provider, but its
service-policy evaluator does not hear the audio. The current policy compiler
also rejected structured response checks against the provider's full
`event:data` payload during the feasibility canary.

Encoding `ALLOW` into assistant text made a canary enforceable, but it conflated
application data with a security decision and depended on a policy surface that
cannot independently validate the audio.

## Decision

VoiceGuard is the semantic enforcement point:

- It returns HTTP 200 only for a complete, audited `ALLOW`.
- `DENY`, `REVIEW`, dependency failure, policy failure, replay, and malformed
  output return non-2xx and never contain a transcript.
- The Gateway Model Service has exactly one destination and no fallback.
- Applications can invoke only the Model Service.
- Gateway policies may add independent metadata or text controls, but are not
  part of the voice-semantic safety claim.

The HTTP success boundary is intentionally equivalent to transcript release.

## Consequences

- Gateway remains useful for identity, Unity Catalog permissions, routing,
  quotas, and usage governance.
- A compromised or inaccurate VoiceGuard provider remains a common-mode risk;
  Gateway cannot correct its semantic false allows.
- Model and policy quality must be established by per-language evaluation and
  continuous canaries.
- Any future fallback must be another fully equivalent VoiceGuard deployment,
  never a raw model or unguarded provider.
- A future Gateway structured policy capability may add defense in depth, but
  does not move semantic ownership out of VoiceGuard.
