# Gateway policy attachments

## VoiceGuard Model Service

Attach only:

1. `envelope_on_call.sql` as a custom ON CALL policy.
2. Rate limits already in `model-service.json.tmpl`.

Do **not** attach built-in Unsafe Content, Jailbreak, PII, hallucination, or
LLM-as-a-judge to the VoiceGuard audio route. Those evaluators do not receive
audio and would create a false sense of voice coverage.

Do **not** add a fallback destination or an inference table that stores WAV.

## Peer text Model Services

Templates in this directory are for **LLM / post-ALLOW transcript** routes:

- `peer_text_service.md` — how to attach Databricks built-ins, custom SQL,
  LLM-as-a-judge, OSS judges, and vendor providers.
- `peer_block_empty_response.sql` — example text-only custom policy.

OSS and vendor binaries stay out of the VoiceGuard container.

## Grants

Applications receive `EXECUTE` only on the VoiceGuard Model Service, not on the
Model Provider Service, origin, STT endpoint, or evaluator.
