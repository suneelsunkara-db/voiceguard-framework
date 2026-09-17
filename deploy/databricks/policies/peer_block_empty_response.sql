-- Example custom policy for a *text* Model Service (LLM or post-ALLOW transcript).
-- Do not attach this as a substitute for VoiceGuard on the audio route.

CREATE OR REPLACE FUNCTION ${CATALOG}.${SCHEMA}.peer_block_empty_response(event VARIANT)
RETURNS STRING
LANGUAGE SQL
COMMENT 'Text peer: deny empty model responses. Not a voice control.'
RETURN
  CASE
    WHEN event:type::string = 'response'
      AND (
        event:context.message::string IS NULL
        OR LENGTH(TRIM(event:context.message::string)) = 0
      )
      THEN 'DENY'
    ELSE 'ALLOW'
  END;
