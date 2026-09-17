# Unity AI Gateway feasibility evidence

Date: 15 September 2026

This evidence predates the standalone framework implementation. It used an
isolated canary only to falsify Gateway integration assumptions. All canary
resources were deleted afterward.

## Proven live

- A 629,804-byte, 6.56-second WAV crossed a managed Unity AI Gateway Model
  Service and custom Model Provider Service to an isolated provider.
- The provider invoked Qwen3-ASR and returned the correct transcript.
- Gateway preserved standard Chat Completion fields and a custom top-level
  `voiceguard` object.
- Provider `DENY` and intentionally malformed outputs were blocked by an
  ON RESULT policy.
- External URLs, stereo WAV, trailing polyglot bytes, extra content blocks, and
  oversized audio were rejected.
- No unmanaged passthrough or raw-payload inference table was used.
- The canary provider origin was restricted to its owner and workspace admins.

## Falsified

- A deterministic English credential rule blocked only English in a ten-language
  spoken test. Chinese, Thai, Indonesian, Arabic, Spanish, French, Hindi,
  Japanese, and Korean were false allows: 1/10 blocked, 9/10 false allows.
- Structured `event:data` response checks could not be transpiled by the current
  service-policy CEL compiler. The canary temporarily used a text prefix; the
  standalone framework explicitly does not retain that workaround.
- A Databricks App origin required a one-hour OAuth bearer that a custom Provider
  Service could not refresh. The standalone design therefore requires an
  independent origin with a stable rotatable API key.
- Three direct and three Gateway-routed samples were dominated by STT latency:
  direct median 5.285 seconds and Gateway median 5.034 seconds. The sample was
  too small to estimate Gateway overhead, but total turn latency was unsuitable
  for natural realtime interaction.
- No documented Gateway duplex audio WebSocket path was found.

## Architectural consequence

VoiceGuard itself is the semantic enforcement point. It returns non-2xx for
`DENY`, `REVIEW`, or `ERROR`, and the Gateway Model Service has no fallback.
Gateway supplies governance and route mediation but is not represented as
independently hearing or classifying the audio.

Production remains blocked until a versioned semantic evaluator passes
per-language held-out speech thresholds and all route, fault, load, privacy, and
recovery gates in the main README.
