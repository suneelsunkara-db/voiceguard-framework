# VoiceGuard v1 certification

Status: **BLOCKED — framework only, not a production safety control**

No deployment may set `VOICEGUARD_POLICY_VERSION` to a release identifier or
enable a language until every mandatory gate below has signed evidence.

## Evidence ledger

| Gate | Required evidence | Status |
|---|---|---|
| Language safety quality | `voiceguard/release-evaluation/v1` report for every language and attack family | Not run |
| Benign false positives | Per-language FPR upper 95% bound at or below release threshold | Not run |
| Malicious true positives | Per-language/family TPR lower 95% bound at or above release threshold | Not run |
| Gateway route closure | Model Service read-back; app has no provider or raw STT privilege | Not run |
| Failure closure | ASR, Qwen Model Service, Lakebase nonce/ledger, timeout, malformed-output tests expose no transcript | Unit coverage only |
| Gateway canary | ALLOW, card DENY, stream reject, language DENY, replay DENY, evaluator 503 | Not run |
| Load and latency | Published p50/p95/p99 plus saturation and timeout evidence | Not run |
| Retention/deletion/recovery | Tested runbook and recovery-point/recovery-time results | Not run |
| Credential rotation | New-key overlap and old-key rejection evidence | Not run |
| Supply chain | `uv.lock`, SBOM, vulnerability report, signed runtime artifact | Lock only |
| Independent review | Threat-model and security-review sign-off | Not run |

## Quality evaluation

Prepare a JSONL manifest. Paths are relative to the manifest:

```json
{"id":"safe-001","audio_path":"audio/safe-001.wav","language":"en-US","attack_family":"benign","malicious":false}
{"id":"inj-001","audio_path":"audio/inj-001.wav","language":"en-US","attack_family":"prompt-injection","malicious":true}
```

Run:

```bash
python tools/evaluate_release.py \
  --manifest eval/held-out.jsonl \
  --output evidence/release-evaluation.json
```

Dependency errors fail the run; they never count as successful attack blocks.
The output contains aggregate outcomes only, not transcripts or audio.

## Gateway deployment and canary

Use `deploy/databricks/reconcile.py`. After the UI-only envelope policy is
attached, rerun with `--require-envelope-policy`.

Then:

```bash
python deploy/databricks/canary.py \
  --profile <profile> \
  --model-service <catalog.schema.voiceguard_service> \
  --safe-wav <held-out-safe.wav> \
  --card-wav <held-out-card.wav> \
  --output evidence/gateway-canary.json
```

For the evaluator failure probe, intentionally disable the evaluator in an
isolated environment and run the same command with `--evaluator-down`. Never
add a request header or production backdoor that simulates dependency failure.

## Sign-off

Required signers:

- VoiceGuard policy owner
- Databricks/Unity AI Gateway owner
- Security reviewer independent of implementation
- Privacy/data-retention owner
- Production operations owner

Until signed, documentation and API metadata must say `unreleased`.
