# Databricks AI Gateway deployment

These templates create two separate Unity Catalog securables:

- A custom Model Provider Service that stores the VoiceGuard origin credential.
- A Model Service that is the only application-facing route.

Do not reuse an application Model Service or attach VoiceGuard to an existing
application deployment script.

## Preconditions

1. VoiceGuard runs on an independent HTTPS origin. Docker is not required.
2. The origin exposes exactly `POST /v1/chat/completions`.
3. A stable random provider API key is stored in the deployment secret manager.
4. The matching SHA-256 digest is present in the server-side tenant profile.
5. VoiceGuard's OAuth M2M service principal can query only the configured Qwen
   STT endpoint and Qwen Model Service, and can access only VoiceGuard's
   Lakebase tables in the current app schema.
6. The application principal has no access to the origin, Provider Service, STT,
   Qwen evaluator service, or VoiceGuard Lakebase tables.

## Reconcile

Use `reconcile.py`, which follows the existing operator-driven Databricks CLI /
SDK deployment style. It keeps the provider key in memory, creates or updates
both UC securables, applies grants, and validates the read-back.

```bash
export VOICEGUARD_ORIGIN=https://voiceguard.example.com
export VOICEGUARD_PROVIDER_API_KEY='<stable-random-secret>'
python deploy/databricks/reconcile.py \
  --profile fe-vm-vdm-classic-rcn6ip \
  --catalog partner_demo_catalog \
  --schema genie_voice_contact_center \
  --provider-service voiceguard_provider \
  --model-service voiceguard_guarded \
  --application-principal '<app-service-principal-client-id>'
```

Policy attachments are UI-only during the current service-policy Beta. After
the first reconcile, attach `policies/envelope_on_call.sql` as ON CALL rank 1
and run the command again with `--require-envelope-policy
catalog.schema.voiceguard_envelope_on_call`.

## Manual create

Render the templates in a secret-aware deployment system. Never commit or print
the rendered provider JSON because it contains a plaintext credential accepted
by the Databricks API.

```bash
databricks api post \
  "/api/2.1/unity-catalog/model-provider-services?parent=schemas/${CATALOG}.${SCHEMA}&model_provider_service_id=${PROVIDER_SERVICE}" \
  --json @rendered-provider.json

databricks api post \
  "/api/2.1/unity-catalog/model-services?parent=schemas/${CATALOG}.${SCHEMA}&model_service_id=${MODEL_SERVICE}" \
  --json @rendered-model-service.json
```

Grant `EXECUTE` to applications only on:

```text
model-services/${CATALOG}.${SCHEMA}.${MODEL_SERVICE}
```

Do not grant application identities access to:

```text
model-provider-services/${CATALOG}.${SCHEMA}.${PROVIDER_SERVICE}
```

## Required configuration assertions

Deployment must fail unless a read-back proves:

- Exactly one external destination points to the expected Provider Service and
  `voiceguard-v1` target.
- No fallback exists.
- No inference payload table exists.
- Unmanaged provider paths are disabled.
- The request rate limit is present.
- Application identities have only Model Service access.

## Existing Genie application cutover

The currently deployed Genie app has direct `CAN_QUERY` resources for Qwen STT
and other ASR endpoints. Do not call that deployment "route closed."

Preserve existing behavior with a staged cutover:

1. Use a dedicated canary principal with only Model Service `EXECUTE`.
2. Pass origin and Gateway canaries without changing Genie.
3. Change Genie voice intake to invoke the VoiceGuard Model Service.
4. Re-run all existing voice regression tests.
5. Only then remove raw STT/ASR app resources and `CAN_QUERY` grants.
6. Run `reconcile.py --forbidden-serving-endpoint ...` for every raw endpoint.

Removing current endpoint grants before step 3 would break existing features;
leaving them after step 5 would preserve a bypass. Neither is acceptable.

## Query

Applications use only the managed Model Service path:

```bash
curl "https://<workspace>/ai-gateway/mlflow/v1/chat/completions" \
  -H "Authorization: Bearer ${DATABRICKS_TOKEN}" \
  -H "Content-Type: application/json" \
  --data @voiceguard-request.json
```

Provider HTTP 403, 409, and 503 outcomes must remain terminal. Do not configure
fallback routing for VoiceGuard.

## Credential rotation

Rotate by updating `config.provider` on the Provider Service and updating the
origin's accepted key set with a bounded overlap. Verify both old-key rejection
and new-key success, then remove the old hash. Do not use a one-hour Databricks
App OAuth token as the stored provider credential.
