-- bed/sql/bed_casino_read_functions.sql
-- One SECURITY DEFINER function for the casino read operations bed serves, dispatched by op
-- name, the same way engine.bed_bank_op dispatches. bed holds no table privileges on the casino
-- tables; it calls engine.bed_casino_read with the SHA-256 digest of the bearer token.
--
-- The acting moniker and sysop status come from the verified token row (engine.__bed_token),
-- never from the request. Each op checks its own rule before it reads:
--   list_tables:  hidden tables are shown only to a sysop. Optional args.game_type filters by type.
--   get_table:    hidden tables are shown only to a sysop. args.moniker names the table.
-- An unknown op is refused. Each call returns one jsonb result with success and the op's data.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_casino_read(p_token_hash bytea, p_op text, p_args jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_sysop boolean;
    v_rows jsonb;
    v_row jsonb;
BEGIN
    SELECT t.moniker INTO v_actor
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_casino_read(%): no live session for this token', p_op;
    END IF;
    v_sysop := engine.bed_is_sysop(v_actor) IS TRUE;

    IF p_op = 'list_tables' THEN
        SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.moniker), '[]'::jsonb) INTO v_rows
        FROM (
            SELECT moniker, type, minimumbet, maximumbet, ownermoniker, ownersince,
                   accountid, cheat, cheatpercent, attrs, shoe_cards, shoe_uses,
                   location, status, hidden
            FROM casino.__table
            WHERE (p_args->>'game_type' IS NULL OR type = p_args->>'game_type')
              AND (v_sysop OR hidden IS NULL OR hidden = false)
        ) x;
        RETURN jsonb_build_object('success', true, 'tables', v_rows);
    END IF;

    IF p_op = 'get_table' THEN
        SELECT to_jsonb(x) INTO v_row
        FROM (
            SELECT moniker, type, minimumbet, maximumbet, ownermoniker, ownersince,
                   accountid, cheat, cheatpercent, attrs, shoe_cards, shoe_uses,
                   location, status, hidden
            FROM casino.__table
            WHERE moniker = p_args->>'moniker'
              AND (v_sysop OR hidden IS NULL OR hidden = false)
        ) x;
        RETURN jsonb_build_object('success', true, 'table', v_row);
    END IF;

    RAISE EXCEPTION 'bed_casino_read: unknown op %', p_op;
END;
$$;

ALTER FUNCTION engine.bed_casino_read(bytea, text, jsonb) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_casino_read(bytea, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_casino_read(bytea, text, jsonb) TO bed;
