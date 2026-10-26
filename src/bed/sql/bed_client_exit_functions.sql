-- bed/sql/bed_client_exit_functions.sql
-- Records why a client ended. bed holds no insert rights on engine.__actionlog; it calls
-- this SECURITY DEFINER function with the SHA-256 digest of the bearer token, the moniker,
-- the reason, and the client's address. The function checks the moniker against the verified
-- token row, then writes a 'client_exit' row through engine.log_action, which validates the
-- reason against the fixed vocabulary (quit, eof, intr, idle, auth_failed, error).
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_client_exit(p_token_hash bytea, p_moniker citext, p_reason text, p_remoteaddr inet)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
DECLARE
    v_actor citext;
BEGIN
    SELECT t.moniker INTO v_actor
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        -- An expired or unknown token cannot be used to write a row. A client that
        -- has lost its session reports the exit to its own log instead.
        RAISE EXCEPTION 'bed_client_exit: no live session for this token';
    END IF;
    IF v_actor IS DISTINCT FROM p_moniker THEN
        RAISE EXCEPTION 'bed_client_exit: forbidden';
    END IF;
    PERFORM engine.log_action(p_moniker, 'client_exit', p_remoteaddr, p_reason);
END
$$;

ALTER FUNCTION engine.bed_client_exit(bytea, citext, text, inet) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_client_exit(bytea, citext, text, inet) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_client_exit(bytea, citext, text, inet) TO bed;
