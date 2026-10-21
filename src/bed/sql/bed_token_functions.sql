-- bed/sql/bed_token_functions.sql
-- Access functions for engine.__bed_token. bed calls these; it holds no table
-- privileges. Each function is owned by zoid6 and runs with zoid6's rights
-- (SECURITY DEFINER) under a fixed search_path. Each takes the SHA-256 digest
-- of the bearer token (token_hash), never the token itself.
--
-- Installed by ``bed --install`` as the owner, after bed_token.sql. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_token_put(
    p_token_hash bytea,
    p_moniker citext,
    p_session_id text,
    p_issued_at double precision,
    p_expires_at double precision,
    p_is_sysop boolean,
    p_bed_instance_id text,
    p_websocket_id text,
    p_claims jsonb,
    p_loginid text
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
    INSERT INTO engine.__bed_token
        (token_hash, moniker, session_id, issued_at, expires_at,
         is_sysop, bed_instance_id, websocket_id, claims, loginid)
    VALUES (p_token_hash, p_moniker, p_session_id,
            to_timestamp(p_issued_at), to_timestamp(p_expires_at),
            p_is_sysop, p_bed_instance_id, p_websocket_id, p_claims, p_loginid)
    ON CONFLICT (token_hash) DO UPDATE SET
        moniker = EXCLUDED.moniker,
        session_id = EXCLUDED.session_id,
        issued_at = EXCLUDED.issued_at,
        expires_at = EXCLUDED.expires_at,
        is_sysop = EXCLUDED.is_sysop,
        bed_instance_id = EXCLUDED.bed_instance_id,
        websocket_id = EXCLUDED.websocket_id,
        claims = EXCLUDED.claims,
        loginid = EXCLUDED.loginid
$$;

CREATE OR REPLACE FUNCTION engine.bed_token_get(p_token_hash bytea)
RETURNS TABLE (
    moniker citext,
    session_id text,
    issued_at double precision,
    expires_at double precision,
    is_sysop boolean,
    bed_instance_id text,
    websocket_id text,
    claims jsonb,
    loginid text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
    SELECT t.moniker, t.session_id,
           EXTRACT(EPOCH FROM t.issued_at)::double precision,
           EXTRACT(EPOCH FROM t.expires_at)::double precision,
           t.is_sysop, t.bed_instance_id, t.websocket_id, t.claims, t.loginid
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now()
$$;

CREATE OR REPLACE FUNCTION engine.bed_token_delete(p_token_hash bytea)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
    WITH d AS (
        DELETE FROM engine.__bed_token WHERE token_hash = p_token_hash RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM d)
$$;

CREATE OR REPLACE FUNCTION engine.bed_token_gc(p_now double precision DEFAULT NULL)
RETURNS bigint
LANGUAGE sql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
    WITH d AS (
        DELETE FROM engine.__bed_token
        WHERE expires_at <= COALESCE(to_timestamp(p_now), now())
        RETURNING 1
    )
    SELECT COUNT(*) FROM d
$$;

ALTER FUNCTION engine.bed_token_put(bytea, citext, text, double precision, double precision, boolean, text, text, jsonb, text) OWNER TO zoid6;
ALTER FUNCTION engine.bed_token_get(bytea) OWNER TO zoid6;
ALTER FUNCTION engine.bed_token_delete(bytea) OWNER TO zoid6;

REVOKE ALL ON FUNCTION engine.bed_token_put(bytea, citext, text, double precision, double precision, boolean, text, text, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.bed_token_get(bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.bed_token_delete(bytea) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION engine.bed_token_put(bytea, citext, text, double precision, double precision, boolean, text, text, jsonb, text) TO bed;
GRANT EXECUTE ON FUNCTION engine.bed_token_get(bytea) TO bed;
GRANT EXECUTE ON FUNCTION engine.bed_token_delete(bytea) TO bed;

ALTER FUNCTION engine.bed_token_gc(double precision) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_token_gc(double precision) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_token_gc(double precision) TO bed;
