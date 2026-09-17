# Peer guardrails on text Model Services

Use this after VoiceGuard has released a transcript (`HTTP 200`). The caller
sends **text** to a separate Model Service. That service may mix:

| Attachment | How | Notes |
|---|---|---|
| Built-in unsafe / PII / jailbreak / hallucination | Gateway UI → Guardrail type = built-in | Text only |
| Custom SQL | `peer_block_empty_response.sql` or tenant-specific functions | No `ai_query`; no regex if the compiler rejects it |
| LLM-as-a-judge | Guardrail type = Custom → LLM-as-a-judge | Do not write ALLOW/DENY in the prompt |
| Llama Prompt Guard 2 | Serve as its own Model Service; app or judge calls it | Do not pip-install into VoiceGuard |
| Llama Guard | Separate Model Service | Calibrate locales before enablement |
| Presidio | Separate HTTP/Model Service for recognizers | Deterministic packs remain authoritative |
| Vendor OpenAI-compatible filter | Additional Model Provider Service | Keep off the audio path |

Routing rule: if VoiceGuard returned 403/409/503, the app must not call the
text Model Service with a guessed transcript.
