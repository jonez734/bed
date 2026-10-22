-- bed/sql/bed_sysop_check.sql
-- Sysop check for bed's session-bound functions.
--
-- engine.checkmemberflag(flag_name, membermoniker) is not SECURITY DEFINER and
-- bed holds no member_flag privilege, so bed cannot call it. This wrapper is
-- zoid6-owned and SECURITY DEFINER, and passes the arguments in the order the
-- function expects: flag first, moniker second.
--
-- Installed by ``bed --install`` as the owner, before the functions that use it.
-- Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_is_sysop(p_moniker citext)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
    SELECT COALESCE(engine.checkmemberflag('sysop', p_moniker::text), false)
$$;

ALTER FUNCTION engine.bed_is_sysop(citext) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_is_sysop(citext) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_is_sysop(citext) TO bed;
