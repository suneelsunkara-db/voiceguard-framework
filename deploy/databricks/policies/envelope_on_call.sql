-- Envelope ON CALL policy for the VoiceGuard Model Service.
-- This function does not hear audio and is not semantic voice enforcement.
-- Attach as Guardrail type = Custom, Phase = Input (ON CALL).
-- If the workspace CEL subset rejects a path, keep the function compiling;
-- do not encode ALLOW in assistant text as a workaround.

CREATE OR REPLACE FUNCTION ${CATALOG}.${SCHEMA}.voiceguard_envelope_on_call(event VARIANT)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Reject empty VoiceGuard calls. Audio semantics stay in the VoiceGuard provider.'
RETURN
  CASE
    WHEN event:type::string IS NULL THEN 'DENY'
    WHEN event:type::string = 'request'
      AND (
        event:context.message::string IS NULL
        OR LENGTH(event:context.message::string) = 0
      )
      THEN 'DENY'
    ELSE 'ALLOW'
  END;
