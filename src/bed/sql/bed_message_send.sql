-- bed/sql/bed_message_send.sql
-- Session-bound message send for bed (direct recipients, @everyone, one-level groups).
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
    v_allowed citext[];
    v_limit integer;
    v_hour timestamp;
    v_count integer;
    v_expanded citext[];
    v_token text;
    v_group_id integer;
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

    -- Expand recipient tokens. Direct monikers pass through. @everyone and
    -- @<group> expand here (one level). @here needs the host's location
    -- resolver, which the database does not have, so it is refused. Nested
    -- groups are refused rather than flattened.
    v_expanded := ARRAY[]::citext[];
    FOREACH v_recipient IN ARRAY p_recipients LOOP
        IF left(v_recipient::text, 1) <> '@' THEN
            v_expanded := v_expanded || v_recipient;
            CONTINUE;
        END IF;

        v_token := lower(substr(v_recipient::text, 2));

        IF v_token = 'here' THEN
            RAISE EXCEPTION 'bed_message_send: @here is not yet supported for bed sends';
        ELSIF v_token = 'everyone' THEN
            IF NOT (engine.checkmemberflag(v_sender, 'sysop') IS TRUE) THEN
                RAISE EXCEPTION 'bed_message_send: only sysops may address @everyone';
            END IF;
            v_expanded := v_expanded || ARRAY(
                SELECT m.moniker FROM engine.__member m WHERE m.approved = TRUE
            );
        ELSE
            SELECT g.id INTO v_group_id FROM engine.__message_group g WHERE g.name = substr(v_recipient::text, 2);
            IF v_group_id IS NULL THEN
                RAISE EXCEPTION 'bed_message_send: unknown group %', v_recipient;
            END IF;
            IF NOT (engine.checkmemberflag(v_sender, 'sysop') IS TRUE)
               AND NOT EXISTS (
                   SELECT 1 FROM engine.__message_group_member gm
                   WHERE gm.group_id = v_group_id AND gm.member_moniker = v_sender
               ) THEN
                RAISE EXCEPTION 'bed_message_send: only members of % (or sysops) may address it', v_recipient;
            END IF;
            IF EXISTS (
                SELECT 1 FROM engine.__message_group_member gm
                WHERE gm.group_id = v_group_id AND gm.member_moniker::text LIKE '@%'
            ) THEN
                RAISE EXCEPTION 'bed_message_send: nested groups are not yet supported (%)', v_recipient;
            END IF;
            v_expanded := v_expanded || ARRAY(
                SELECT gm.member_moniker FROM engine.__message_group_member gm WHERE gm.group_id = v_group_id
            );
        END IF;
    END LOOP;

    -- De-duplicate while keeping first-seen order (matches resolve_recipients).
    SELECT COALESCE(array_agg(x ORDER BY ord), '{}') INTO v_expanded
    FROM (
        SELECT x, min(ord) AS ord FROM unnest(v_expanded) WITH ORDINALITY AS t(x, ord) GROUP BY x
    ) d;

    -- Drop recipients who have blocked the sender. A blocked recipient is
    -- skipped, not an error, matching the service layer.
    SELECT COALESCE(array_agg(r ORDER BY r), '{}') INTO v_allowed
    FROM unnest(v_expanded) AS r
    WHERE NOT EXISTS (
        SELECT 1 FROM engine.__message_block b
        WHERE b.blocker_moniker = r AND b.blocked_moniker = v_sender
    );

    IF cardinality(v_allowed) = 0 THEN
        RAISE EXCEPTION 'bed_message_send: no recipients accepted this message';
    END IF;

    -- Reserve one rate slot for this sender and channel in the current hour.
    -- Done after the block check so a blocked send never consumes a slot.
    SELECT rate_limit_per_hour INTO v_limit
    FROM engine.__message_type WHERE type_name = p_channel;

    IF v_limit IS NOT NULL AND v_limit > 0 THEN
        v_hour := date_trunc('hour', now());
        INSERT INTO engine.__message_rate_limit
            (sender_moniker, message_type, hour_bucket, message_count)
        VALUES (v_sender, p_channel, v_hour, 1)
        ON CONFLICT (sender_moniker, message_type, hour_bucket)
        DO UPDATE SET message_count = engine.__message_rate_limit.message_count + 1
        WHERE engine.__message_rate_limit.message_count < v_limit
        RETURNING message_count INTO v_count;

        IF v_count IS NULL THEN
            RAISE EXCEPTION 'bed_message_send: rate limit reached for this channel this hour';
        END IF;
    END IF;

    INSERT INTO engine.__message (channel, sender_moniker, content, urgency, source)
    VALUES (p_channel, v_sender, p_content, p_urgency::engine.notify_urgency_enum, 'member')
    RETURNING id INTO v_id;

    INSERT INTO engine.__message_recipient (message_id, recipient_moniker)
    SELECT v_id, r FROM unnest(v_allowed) AS r
    ON CONFLICT DO NOTHING;

    RETURN v_id;
END
$$;

ALTER FUNCTION engine.bed_message_send(bytea, text, text, text, citext[]) OWNER TO zoid6;

REVOKE ALL ON FUNCTION engine.bed_message_send(bytea, text, text, text, citext[]) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION engine.bed_message_send(bytea, text, text, text, citext[]) TO bed;
