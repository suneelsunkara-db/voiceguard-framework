# ADR 0002: Locked v1 invariants cannot be traded

Status: Accepted
Date: 17 September 2026

## Context

Voice and LLM vendors commonly ship streaming, fail-open fallbacks, remote
audio URLs, prompt-tunable moderation, and mixed identity/application
credentials. Those are product choices for assistants. They are not acceptable
substitutes for an inbound utterance gate.

A later request to "match vendor features" must not weaken v1 safety
properties. Streaming is the canonical example: a partial utterance cannot be
canonicalized, replay-claimed, language-checked, evaluated, or audited as one
decision.

## Decision

The following are frozen v1 invariants. They are not flags, backlog items, or
compatibility shims. Removing or softening any of them requires a new major
contract and a new security evaluation. It is not a patch.

1. **Completed utterance only.** `stream=true` is rejected. There is no
   streaming `ALLOW`, no partial transcript, and no chunk-wise policy.
2. **Fail closed.** Missing language coverage, replay store, ASR, required
   policy, semantic evaluator, or ledger cannot produce `ALLOW` or a
   transcript.
3. **HTTP 200 is transcript release.** `DENY`/`REVIEW`/`ERROR` are non-2xx and
   contain no transcript. No text prefix, sidecar, or Gateway CEL workaround
   encodes the decision.
4. **Gateway is the governed route, not the ear.** One Model Service
   destination, no fallback, no passthrough, no raw-audio inference table.
   Semantic enforcement stays in VoiceGuard.
5. **Tenant and policy are server-bound.** API-key profile supplies tenant,
   bundle, and enforced languages. Request JSON cannot select them.
6. **ASR auto-detects.** Caller language is an assertion. Mismatch and
   unevaluated languages are `DENY`. They are never `ALLOW`.
7. **Canonical audio only.** One inline PCM16 WAV data URI. No remote URLs,
   no codec ambiguity, no polyglot containers.
8. **One-time nonce.** `(tenant, nonce)` is claimed once. Replay is `DENY`.
   Replay-store failure is `ERROR`.
9. **Required version-pinned evaluator.** Version drift, malformed output, or
   unbounded confidence fails closed.
10. **Metadata-only ledger.** No raw audio or transcript in logs or audit.
11. **Independent origin.** No application (including Genie) owns credentials,
    runtime, or policy lifecycle. Databricks App OAuth is not the provider key.
12. **Independent product.** TTS / output gating is a different contract, not a
    reason to relax inbound invariants.

## Consequences

- Feature requests that need streaming, fail-open, URL fetch, or client-chosen
  policy are out of scope for `voiceguard-v1`.
- Latency and DX improvements must preserve these properties (for example:
  faster complete-utterance ASR, not token streaming).
- A future `voiceguard-v2` may exist only as a separately versioned, separately
  evaluated product. It does not silently extend v1.
