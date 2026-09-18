# Unity AI Gateway guardrail program

VoiceGuard is the custom **voice** guardrail on Unity AI Gateway. It is not a
Genie sidecar and it does not replace Gateway. Applications call a Model Service
with `EXECUTE` only. Built-in, custom SQL, LLM-as-a-judge, OSS, and vendor
controls attach as **peers**. They never receive the audio bytes.

Frozen invariants: `docs/adr/0001-gateway-enforcement-boundary.md` and
`docs/adr/0002-locked-v1-invariants.md`.

## Who hears what

| Slot | Hears audio | Sees | Allowed on VoiceGuard Model Service |
|---|---|---|---|
| VoiceGuard custom Model Provider | Yes | Canonical WAV + STT transcript internally | Required destination |
| Custom SQL service policy | No | `event` VARIANT; typically `event:context.message` text | Envelope/metadata ON CALL only |
| Built-in unsafe / PII / jailbreak | No | Text extracted by Databricks evaluators | No (not voice semantics) |
| LLM-as-a-judge | No | Text plus judge prompt | No on the audio route |
| OSS text judges (Prompt Guard, Llama Guard, Presidio) | No | Transcript **after** VoiceGuard `ALLOW`, on a **text** Model Service | Peer only |
| Vendor filters (OpenAI-compatible) | No | Text on a vendor Model Provider | Peer only; never on the audio path unless the vendor is VoiceGuard |

Gateway built-in evaluators strip audio. A SQL policy cannot decode WAV, fetch a
URL, or call `ai_query`. Treating those policies as voice enforcement is a
program failure.

## Attach order

```text
Application
  → Model Service (UC EXECUTE, quotas, traces)
      → ON CALL envelope SQL (shape/size only)
      → VoiceGuard origin (semantic gate; HTTP 200 = transcript release)
      → no fallback, no passthrough, no raw-audio inference table

After ALLOW, the application may send the sanitized transcript to a separate
text Model Service that has built-in, SQL, OSS, or vendor policies attached.
That second hop cannot skip VoiceGuard.
```

## Guardrail catalog

| Control | Gateway slot | Owner | Language gate |
|---|---|---|---|
| Completed utterance, PCM16 WAV, no streaming | VoiceGuard origin parser | VoiceGuard | n/a |
| Replay nonce | VoiceGuard-owned schema on approved Lakebase | VoiceGuard | n/a |
| ASR language allowlist | VoiceGuard engine | VoiceGuard | Release-approved tags only |
| Payment-card identifiers | VoiceGuard deterministic policy | VoiceGuard | Digit/Luhn; locale packs at certification |
| Semantic unsafe / injection on speech | VoiceGuard version-pinned evaluator | VoiceGuard | Certified languages only |
| Request envelope (JSON shape, no stream flag if visible) | Custom SQL ON CALL | This program | n/a |
| Chat/LLM jailbreak, hallucination, output PII | Built-in policies on **text** Model Services | Databricks | Evaluator model card |
| Org-specific text strings | Custom SQL on **text** Model Services | Tenant | Text only |
| Semantic text classifier | LLM-as-a-judge on **text** Model Services | Tenant + Databricks | Judge language |
| Prompt injection on transcript | Llama Prompt Guard 2 as a **separate** Model Service | OSS peer | Reported multilingual; calibrate |
| Unsafe transcript categories | Llama Guard as a **separate** Model Service | OSS peer | Calibrate per locale |
| Named-entity PII on transcript | Presidio/GLiNER as a **separate** service | OSS peer | Locale recognizers |
| Commercial text filters | Vendor Model Provider on **text** routes | Vendor | Vendor card |

Do not import Presidio, Llama Guard, or vendor SDKs into the VoiceGuard process.

## Hosting

The origin is an independent HTTPS runtime. Databricks Apps are forbidden as
the provider (one-hour OAuth must not be the stored Gateway credential). Docker
is not required. Pin `VOICEGUARD_HOSTING_TARGET=independent-https`.

Production requires a signed runtime artifact, private network,
Gateway-only ingress, and the readiness contract described in
`docs/certification.md`.

## Lab versus production

- Lab attach (English fixtures, isolated catalog) is allowed after origin +
  Model Service exist.
- Production is blocked until `docs/certification.md` is signed. Unevaluated
  languages must not appear in `VOICEGUARD_SUPPORTED_LANGUAGES`.
