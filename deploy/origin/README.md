# VoiceGuard origin deployment contract

The custom-provider architecture requires an HTTPS origin outside Databricks
Apps. This repository deliberately does not invent a hosting platform or add a
Docker requirement. Select an approved runtime and satisfy this contract.

## Process

Install the exact versions in `uv.lock`, build all packages with
`uv build --all-packages`, install `voiceguard-server` and `voiceguard-core`,
and run:

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
- Dedicated Databricks OAuth M2M credentials invoke only the configured Qwen
  STT, governed evaluator Model Service, and Lakebase OAuth credential API.
- `VOICEGUARD_CONFIG` points to the reviewed VoiceGuard-owned
  `config/voiceguard.yaml`.

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

- `voiceguard_core-<version>-py3-none-any.whl`
- `voiceguard_server-<version>-py3-none-any.whl`
- `voiceguard_client-<version>-py3-none-any.whl`
- `voiceguard.cdx.json`
- vulnerability report
- runtime signature / provenance
- the exact `uv.lock`

The runtime must use Python 3.11. Health checks must call `/ready`; process
liveness alone is not sufficient for traffic admission.

## Lakebase migration

Run schema migration as an owner/deployer before starting the runtime:

```bash
python deploy/lakebase/migrate.py \
  --config config/voiceguard.yaml \
  --profile fe-vm-vdm-classic-rcn6ip \
  --runtime-principal '<voiceguard-service-principal-client-id>'
```

The runtime principal receives `CONNECT`, schema `USAGE`, and table
`SELECT`/`INSERT` only. The server validates the tables at startup and does not
perform DDL.

## Production preflight

From the exact runtime environment, after migration and before opening traffic:

```bash
python tools/preflight.py \
  --config /etc/voiceguard/voiceguard.yaml \
  --output /var/lib/voiceguard/preflight.json
```

This validates the release policy version, the authenticated M2M service
principal, Qwen STT readiness, the pinned semantic Model Service route, and both
Lakebase tables. It runs every check and exits non-zero if any check fails. The
report contains exception types only and does not expose credentials or provider
responses.

## Missing deployment input

No approved independent origin platform or URL is configured in this
repository. Live deployment must not proceed until its owner supplies them.
Configured STT, semantic evaluation, and Lakebase may reference approved shared
infrastructure, but VoiceGuard uses its own identity, configuration, schema, and
tables.
