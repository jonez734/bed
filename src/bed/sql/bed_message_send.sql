-- bed/sql/bed_message_send.sql
-- Session-bound message send for bed (slice 1: direct recipients).
--
-- bed calls engine.bed_message_send with the SHA-256 digest of the bearer
-- token. The sender is read from engine.__bed_token for that hash, so bed
-- cannot name a different sender. The function runs as zoid6 (SECURITY
-- DEFINER) with a fixed search_path, and bed holds EXECUTE only.
--
-- Slice 1 accepts direct monikers only. Groups (@group), @here, @everyone and
-- named sources are refused until their slices land.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_message_send(
    p_token_hash bytea,
    p_channel text,
    p_content text,
    p_urgency text,
    p_recipients citext[]
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
DECLARE
    v_sender citext;
    v_id bigint;
    v_recipient citext;
BEGIN
    SELECT t.moniker INTO v_sender
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();

    IF v_sender IS NULL THEN
        RAISE EXCEPTION 'bed_message_send: no live session for this token';
    END IF;

    IF p_recipients IS NULL OR cardinality(p_recipients) = 0 THEN
        RAISE EXCEPTION 'bed_message_send: at least one recipient is required';
    END IF;

    FOREACH v_recipient IN ARRAY p_recipients LOOP
        IF left(v_recipient::text, 1) = '@' THEN
            RAISE EXCEPTION 'bed_message_send: group and broadcast addressing is not yet supported (%)', v_recipient;
        END IF;
    END LOOP;

    INSERT INTO engine.__message (channel, sender_moniker, content, urgency, source)
    VALUES (p_channel, v_sender, p_content, p_urgency::engine.notify_urgency_enum, 'member')
    RETURNING id INTO v_id;

    INSERT INTO engine.__message_recipient (message_id, recipient_moniker)
    SELECT v_id, r FROM unnest(p_recipients) AS r
    ON CONFLICT DO NOTHING;

    RETURN v_id;
END
$$;

ALTER FUNCTION engine.bed_message_send(bytea, text, text, text, citext[]) OWNER TO zoid6;

REVOKE ALL ON FUNCTION engine.bed_message_send(bytea, text, text, text, citext[]) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION engine.bed_message_send(bytea, text, text, text, citext[]) TO bed;
