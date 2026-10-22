-- bed/sql/bed_bank_functions.sql
-- Bank functions for bed. bed holds no table privileges on the bank tables;
-- it calls these zoid6-owned SECURITY DEFINER functions with the SHA-256 digest of
-- the bearer token. The acting moniker and sysop status come from the verified
-- token row (engine.__bed_token), never from the wire message.
--
-- Rules mirror bbsengine6.bank.access():
--   balance: the target must be the caller's own account, or the caller is a sysop.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_bank_balance(p_token_hash bytea, p_moniker citext)
RETURNS TABLE (account_exists boolean, balance numeric)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_row bank.__account%ROWTYPE;
BEGIN
    SELECT t.moniker INTO v_actor
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_bank_balance: no live session for this token';
    END IF;

    IF NOT (engine.bed_is_sysop(v_actor) IS TRUE) AND v_actor IS DISTINCT FROM p_moniker THEN
        RAISE EXCEPTION 'bed_bank_balance: forbidden';
    END IF;

    SELECT * INTO v_row FROM bank.__account a WHERE a.moniker = p_moniker;
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, 0::numeric;
    ELSE
        RETURN QUERY SELECT true, v_row.balance;
    END IF;
END
$$;

ALTER FUNCTION engine.bed_bank_balance(bytea, citext) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_bank_balance(bytea, citext) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_bank_balance(bytea, citext) TO bed;
