# Security status

VoiceGuard `0.1.0` is pre-production framework scaffolding. It is not an
approved safety control and currently has no certified language policy release.

## Mandatory deployment properties

- Public internet clients cannot reach the provider origin.
- Only the expected Gateway provider credential is accepted.
- Application principals can execute only the Gateway Model Service.
- Databricks OAuth is scoped to the configured Qwen STT endpoint, Qwen Model
  Service, and Lakebase; no extra datastore or evaluator credential exists.
- Egress is restricted to the approved Databricks workspace and Lakebase.
- Raw audio and transcripts are absent from infrastructure, access, exception,
  inference, and audit logs.
- Provider non-2xx outcomes are terminal and no fallback releases a transcript.

## Security-relevant changes

Changes to request parsing, codec support, limits, authentication, tenant
profiles, replay behavior, policy aggregation, language allowlists, evaluator
contracts, thresholds, dependency versions, routing, or fallback invalidate the
previous release evaluation.

## Before the first production release

- Fuzz at least one million mutated request/container cases with zero ambiguous
  accepts or parser differentials.
- Pass per-language malicious and benign confidence bounds documented in the
  README.
- Complete SSRF, resource exhaustion, prompt injection, replay, spoof,
  cross-tenant, fault-injection, load, privacy, and recovery testing.
- Conduct independent code and deployment security reviews.
- Produce an SBOM, vulnerability scan, signed runtime artifact, pinned dependency
  lock, and reproducible build evidence.

Do not report a vulnerability using raw production audio or transcripts. Use
synthetic fixtures and include only hashes when identifying affected requests.
