-- bed/sql/bed_lastlogin_functions.sql
-- Records a password login for bed. bed holds no table privileges on engine.__member.
-- The function takes the SHA-256 digest of the bearer token, the moniker, and the client's
-- socket address. It reads the acting moniker from the verified token row, and returns the
-- lastlogin value from before this login. It then sets lastlogin = now() and
-- lastloginfrom = the client address, and writes a 'login' row through engine.log_action.
-- Only password logins call it; token reconnects do not.
--
-- Installed by ``bed --install`` as the owner. Idempotent.
--
-- The argument list changed (client address added). A sysop drops the old two-argument
-- function once, before this file is loaded: see handbook/specs/pg-ident-auth.md.

CREATE OR REPLACE FUNCTION engine.bed_touch_lastlogin(p_token_hash bytea, p_moniker citext, p_remoteaddr inet)
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
    UPDATE engine.__member
       SET lastlogin = now(), lastloginfrom = p_remoteaddr
     WHERE moniker = p_moniker;
    PERFORM engine.log_action(p_moniker, 'login', p_remoteaddr, NULL);
    RETURN v_previous;
END
$$;

ALTER FUNCTION engine.bed_touch_lastlogin(bytea, citext, inet) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_touch_lastlogin(bytea, citext, inet) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_touch_lastlogin(bytea, citext, inet) TO bed;
