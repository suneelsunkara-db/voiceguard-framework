# VoiceGuard origin deployment contract

The custom-provider architecture requires an HTTPS origin outside Databricks
Apps. This repository deliberately does not invent a hosting platform or add a
Docker requirement. Select an approved runtime and satisfy this contract.

## Process

Install the exact versions in `uv.lock`, build the wheel with `uv build`, and
run:

```bash
voiceguard
```

The process listens on `0.0.0.0:8080`. Terminate TLS at the approved ingress.
Only these routes exist:

- `GET /health` — process liveness
- `GET /ready` — required dependency readiness
- `POST /v1/chat/completions` — provider contract

## Required secret injection

Inject every variable in `.env.example` from the runtime secret manager. Do not
render secrets into source, deployment logs, or shell history.

- The Gateway provider key is stored only as SHA-256 hashes in
  `VOICEGUARD_TENANTS`.
- Databricks OAuth M2M credentials invoke the current app's Qwen STT, governed
  Qwen Model Service, and Lakebase OAuth credential API.
- The runtime receives a reviewed copy of the current app's `config.yaml`; it
  does not invent parallel resource names.

## Network policy

Ingress:

- Allow Unity AI Gateway egress to `/v1/chat/completions`.
- Allow the deployment readiness probe to `/ready`.
- Deny general public access. Authentication remains mandatory even on the
  allowlisted path.

Egress:

- Approved Databricks workspace host only: Qwen STT, the Qwen Unity AI Gateway
  Model Service, and Lakebase.

No other egress is permitted.

## Release artifact

Deploy the wheel generated from the reviewed commit, verify its SHA-256 against
the CI artifact, and retain:

- `voiceguard_framework-<version>-py3-none-any.whl`
- `voiceguard.cdx.json`
- vulnerability report
- runtime signature / provenance
- the exact `uv.lock`

The runtime must use Python 3.11. Health checks must call `/ready`; process
liveness alone is not sufficient for traffic admission.

## Missing deployment input

No approved independent origin platform or URL is configured in this
repository. Live deployment must not proceed until its owner supplies them.
STT, semantic evaluation, nonce storage, and the metadata ledger reuse the
resources already configured in the current app.
