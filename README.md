# VoiceGuard Framework

VoiceGuard is an independent, fail-closed service for evaluating completed voice
utterances before an application consumes their transcript.

It is not part of Genie Voice Agent. The framework has its own API, runtime,
credentials, policy lifecycle, replay store, audit sink, deployment, and tests.
Applications integrate through a governed AI Gateway Model Service.

## Non-negotiable design rules

These are frozen v1 invariants (`docs/adr/0002-locked-v1-invariants.md`).
They are not optional, not feature-flagged, and not traded for vendor parity
(streaming, fail-open fallbacks, remote audio URLs, or mixed app credentials).

- No runtime import or deployment hook into an application repository. The
  deployer materializes the same reviewed `config.yaml` used by the current app.
- No direct application access to the provider origin, Model Provider Service,
  STT endpoint, semantic evaluator, or audit backend.
- No unmanaged Gateway passthrough or fallback destination.
- No raw-audio inference table and no raw audio or transcript in framework logs.
- No `ALLOW` when language support, replay storage, ASR, semantic evaluation,
  required policy, or audit recording is unavailable.
- No streaming `ALLOW`; the complete utterance is canonicalized first.
  `stream=true` is rejected. Partial audio is not a valid request.
- No provider-controlled text prefix used as a Gateway policy workaround.

## Architecture

```text
Application
  │ Databricks identity; EXECUTE only on Model Service
  ▼
Unity AI Gateway Model Service
  │ managed Chat Completions; no fallback/passthrough/payload table
  ▼
Custom Model Provider Service
  │ stable rotatable API key
  ▼
VoiceGuard HTTPS service
  ├── strict request and WAV/PCM16 canonicalization
  ├── Lakebase/Postgres one-time nonce claim
  ├── OAuth M2M call to versioned STT
  ├── deterministic invariant policies
  ├── required governed Qwen Model Service evaluator
  └── metadata-only Lakebase decision ledger
```

The core package depends only on framework contracts. The production adapters
reuse the current app's Databricks OAuth, Qwen STT, governed Qwen Model Service,
and Lakebase/Postgres configuration.

## Enforcement behavior

The provider itself is the semantic enforcement point because Gateway policies
do not receive audio. Gateway remains the authenticated governed route.

- `ALLOW`: HTTP 200 OpenAI Chat Completion containing the sanitized transcript
  and a top-level structured `voiceguard` decision.
- `DENY`: HTTP 403; no transcript is returned.
- `REVIEW`: HTTP 409; no transcript is returned.
- `ERROR`: HTTP 503; no transcript is returned.
- Invalid transport or audio: HTTP 400.

With no Model Service fallback, all non-200 provider outcomes stop the request.
This uses normal upstream HTTP semantics, not a text marker or undocumented
service-policy payload expression.

## Request profile

VoiceGuard intentionally supports a strict subset of Chat Completions:

```json
{
  "model": "voiceguard-v1",
  "messages": [{
    "role": "user",
    "content": [
      {
        "type": "text",
        "text": "{\"request_id\":\"request_123456\",\"nonce\":\"nonce_123456\",\"language\":\"en-US\"}"
      },
      {
        "type": "audio_url",
        "audio_url": {
          "url": "data:audio/wav;base64,<canonical-base64>"
        }
      }
    ]
  }]
}
```

Accepted audio is one complete mono, uncompressed PCM16 RIFF/WAVE utterance at
8, 16, 24, or 48 kHz. Remote URLs, multiple messages, mixed content, duplicate
JSON keys, unknown fields, streaming, trailing bytes, and format ambiguity are
rejected.

Tenant identity, policy bundle, and enforced language set come from the
server-side API-key profile, never from request JSON. Each `(tenant, nonce)` can
be claimed once. A caller's optional language is only an assertion: ASR always
auto-detects, and a mismatch is denied.

## Runtime configuration

Copy `.env.example` into your secret manager. Do not put plaintext credentials
in source control.

The service intentionally refuses to start without:

- Provider API-key hashes and a release-approved language allowlist.
- The current app's reviewed deployment config plus Databricks OAuth M2M.
- The configured Qwen STT endpoint and governed Qwen Model Service.
- The configured Lakebase project, database, and schema for atomic nonces and
  metadata-only decisions.

The semantic evaluator crosses the existing governed Qwen Model Service. Its
single destination is verified before evaluation and its response must exactly match
`voiceguard/semantic-assessment/v1`, use the configured model version, and
provide a bounded confidence. Version drift or malformed output fails closed.

## Databricks integration

Templates are under `deploy/databricks/`.

1. Deploy VoiceGuard to an independent HTTPS runtime. The framework does not
   prescribe Docker; the origin contract, credential lifecycle, and network
   controls are what matter.
2. Store a stable, rotatable VoiceGuard API key in a custom Model Provider
   Service. Its URL must be the exact `/v1/chat/completions` route.
3. Create a Model Service with one destination and no fallback.
4. Grant application identities `EXECUTE` only on the Model Service.
5. Do not grant them direct provider, provider-origin, STT, or evaluator access.

Service-policy attachment may still be used for metadata/text controls, but it
is not treated as semantic voice enforcement.

## Release gates

The repository is framework scaffolding, not a production-certified policy
release. Production remains blocked until:

- Every enabled language and attack family meets the published TPR/FPR
  confidence bounds on held-out speech.
- Route-closure tests prove there is no direct or unmanaged bypass.
- Fault injection proves ASR, evaluator, nonce store, ledger, credential,
  timeout, and malformed-output failures release no transcript.
- Load, latency, deletion, retention, and recovery SLOs pass.
- Runtime artifacts and dependencies are pinned, reviewed, and reproducible.
- An external security review and threat-model sign-off complete.

An unsupported or unevaluated language must not be placed in
`VOICEGUARD_SUPPORTED_LANGUAGES`.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```
