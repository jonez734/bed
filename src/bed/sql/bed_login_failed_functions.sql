-- bed/sql/bed_login_failed_functions.sql
-- Records a rejected password login. There is no session yet, so no token is checked; the
-- function only writes a 'login_failed' row through engine.log_action, which bed cannot call
-- directly. It is SECURITY DEFINER, owned by zoid6, and executable by bed only.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_login_failed(p_moniker citext, p_remoteaddr inet)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
BEGIN
    PERFORM engine.log_action(p_moniker, 'login_failed', p_remoteaddr, NULL);
END
$$;

ALTER FUNCTION engine.bed_login_failed(citext, inet) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_login_failed(citext, inet) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_login_failed(citext, inet) TO bed;
