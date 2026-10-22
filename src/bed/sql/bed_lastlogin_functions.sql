-- bed/sql/bed_lastlogin_functions.sql
-- Records a password login for bed. bed holds no table privileges on engine.__member.
-- The function takes the SHA-256 digest of the bearer token, reads the acting moniker from
-- the verified token row, and returns the lastlogin value from before this login. It then sets
-- lastlogin = now(). Only password logins call it; token reconnects do not.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_touch_lastlogin(p_token_hash bytea, p_moniker citext)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_previous timestamptz;
BEGIN
    SELECT t.moniker INTO v_actor
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_touch_lastlogin: no live session for this token';
    END IF;
    IF v_actor IS DISTINCT FROM p_moniker THEN
        RAISE EXCEPTION 'bed_touch_lastlogin: forbidden';
    END IF;

    SELECT m.lastlogin INTO v_previous FROM engine.__member m WHERE m.moniker = p_moniker;
    UPDATE engine.__member SET lastlogin = now() WHERE moniker = p_moniker;
    RETURN v_previous;
END
$$;

ALTER FUNCTION engine.bed_touch_lastlogin(bytea, citext) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_touch_lastlogin(bytea, citext) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_touch_lastlogin(bytea, citext) TO bed;
