-- Read functions that the bed service role calls for message reads.
--
-- bed holds no table privileges on the message tables. These functions are
-- owned by zoid6 and run with zoid6's rights (SECURITY DEFINER). search_path
-- is fixed so the caller cannot redirect the lookup. They are read-only.
--
-- Run by the owner during install (after checkmessage and the message
-- ownership migration). Idempotent.

-- Drop the pre-TTL overload. CREATE OR REPLACE does not remove it, and it
-- would otherwise stay executable by bed without the max_age_days filter.
DROP FUNCTION IF EXISTS engine.bed_list_pending_page(citext, int, timestamptz, bigint);

CREATE OR REPLACE FUNCTION engine.bed_list_pending_page(
    recipient citext,
    lim int,
    before_datestamp timestamptz DEFAULT NULL,
    before_id bigint DEFAULT NULL,
    max_age_days int DEFAULT 0
)
RETURNS TABLE (
    id bigint,
    channel text,
    sender_moniker citext,
    content text,
    data jsonb,
    urgency engine.notify_urgency_enum,
    template text,
    template_vars jsonb,
    datestamp timestamptz,
    status text,
    datedelivered timestamptz,
    dateread timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
    SELECT
        m.id, m.channel, m.sender_moniker, m.content, m.data,
        m.urgency, m.template, m.template_vars, m.datestamp,
        r.status, r.datedelivered, r.dateread
    FROM engine.__message m
    JOIN engine.__message_recipient r ON r.message_id = m.id
    WHERE r.recipient_moniker = recipient
      AND r.status IN ('pending', 'delivered')
      AND (before_datestamp IS NULL OR (m.datestamp, m.id) < (before_datestamp, before_id))
      AND (max_age_days <= 0 OR m.datestamp >= now() - make_interval(days => max_age_days))
    ORDER BY m.datestamp DESC, m.id DESC
    LIMIT lim
$$;

CREATE OR REPLACE FUNCTION engine.bed_count_unread(recipient citext)
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
    SELECT COUNT(*)
    FROM engine.__message_recipient r
    WHERE r.recipient_moniker = recipient
      AND r.status IN ('pending', 'delivered')
$$;

ALTER FUNCTION engine.bed_list_pending_page(citext, int, timestamptz, bigint, int) OWNER TO zoid6;
ALTER FUNCTION engine.bed_count_unread(citext) OWNER TO zoid6;

REVOKE ALL ON FUNCTION engine.bed_list_pending_page(citext, int, timestamptz, bigint, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.bed_count_unread(citext) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION engine.bed_list_pending_page(citext, int, timestamptz, bigint, int) TO bed;
GRANT EXECUTE ON FUNCTION engine.bed_count_unread(citext) TO bed;
