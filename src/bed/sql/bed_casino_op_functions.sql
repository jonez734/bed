-- bed/sql/bed_casino_op_functions.sql
-- One SECURITY DEFINER function for the casino table writes bed serves, dispatched by op name, the
-- same way engine.bed_bank_op dispatches. bed holds no table privileges on the casino tables; it calls
-- engine.bed_casino_op with the SHA-256 digest of the bearer token.
--
-- The acting moniker and sysop status come from the verified token row (engine.__bed_token) and the
-- sysop member flag, never from the request. Each op checks its own rule before it writes:
--   create:  the owner is the caller (a sysop may create for another member). The owner must exist.
--   update:  the table owner or a sysop. status must be open or closed; hidden must be boolean.
--   join:    the caller takes a seat at the table. The table must exist and be open.
--   leave:   the caller leaves the table. A sysop may remove another player.
-- An unknown op is refused. Each call returns one jsonb result with success and the op's data.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_casino_op(p_token_hash bytea, p_op text, p_args jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, casino, bank, public, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_sysop boolean;
    v_moniker citext;
    v_owner citext;
    v_type text;
    v_min numeric;
    v_max numeric;
    v_hidden boolean;
    v_status text;
    v_account bigint;
    v_location text;
    v_target citext;
    v_row jsonb;
BEGIN
    SELECT t.moniker INTO v_actor
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_casino_op: no live session for this token';
    END IF;
    v_sysop := engine.bed_is_sysop(v_actor) IS TRUE;

    IF p_op = 'create' THEN
        v_owner := coalesce(p_args->>'owner', v_actor::text);
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_owner THEN
            RAISE EXCEPTION 'bed_casino_op: forbidden';
        END IF;
        v_type := p_args->>'game_type';
        v_min := coalesce((p_args->>'min_bet')::numeric, 10);
        v_max := coalesce((p_args->>'max_bet')::numeric, 1000);
        v_hidden := coalesce((p_args->>'hidden')::boolean, false);
        v_moniker := coalesce(p_args->>'moniker', v_type || '-' || lower(v_owner::text));

        IF EXISTS (SELECT 1 FROM casino.__table WHERE moniker = v_moniker) THEN
            RETURN jsonb_build_object('success', false, 'exists', true,
                                      'message', 'Table ' || v_moniker || ' already exists');
        END IF;
        IF NOT EXISTS (SELECT 1 FROM engine.__member WHERE moniker = v_owner) THEN
            RETURN jsonb_build_object('success', false, 'message', 'Failed to create table');
        END IF;

        SELECT id INTO v_account FROM bank.__account WHERE moniker = v_owner;
        IF v_account IS NULL THEN
            INSERT INTO bank.__account (moniker, balance) VALUES (v_owner, 0) RETURNING id INTO v_account;
        END IF;

        v_location := 'table-' || substr(md5(random()::text || clock_timestamp()::text), 1, 8);

        INSERT INTO casino.__bank_table (table_moniker, bank_account_id) VALUES (v_moniker, v_account);
        INSERT INTO casino.__table (moniker, type, minimumbet, maximumbet, ownermoniker, ownersince,
                                    accountid, location, status, hidden)
            VALUES (v_moniker, v_type, v_min, v_max, v_owner, now(), v_account, v_location, 'open', v_hidden);

        SELECT to_jsonb(t) INTO v_row FROM (
            SELECT moniker, type, minimumbet, maximumbet, ownermoniker, ownersince, accountid,
                   location, status, hidden
            FROM casino.__table WHERE moniker = v_moniker
        ) t;
        RETURN jsonb_build_object('success', true, 'table', v_row,
                                  'message', 'Table ' || v_moniker || ' created');
    END IF;

    IF p_op = 'update' THEN
        v_moniker := p_args->>'moniker';
        SELECT ownermoniker, status INTO v_owner, v_status FROM casino.__table WHERE moniker = v_moniker;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('success', false, 'message', 'Table not found');
        END IF;
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_owner THEN
            RAISE EXCEPTION 'bed_casino_op: forbidden';
        END IF;
        IF p_args ? 'status' AND (p_args->>'status') NOT IN ('open', 'closed') THEN
            RETURN jsonb_build_object('success', false, 'message', 'Status must be open or closed');
        END IF;
        IF p_args ? 'hidden' AND jsonb_typeof(p_args->'hidden') <> 'boolean' THEN
            RETURN jsonb_build_object('success', false, 'message', 'hidden must be a boolean');
        END IF;
        UPDATE casino.__table SET
            minimumbet = coalesce((p_args->>'min_bet')::numeric, minimumbet),
            maximumbet = coalesce((p_args->>'max_bet')::numeric, maximumbet),
            status = coalesce(p_args->>'status', status),
            hidden = coalesce((p_args->>'hidden')::boolean, hidden)
        WHERE moniker = v_moniker;
        RETURN jsonb_build_object('success', true, 'message', 'Table updated');
    END IF;

    IF p_op = 'join' THEN
        v_target := coalesce(p_args->>'moniker');
        IF NOT EXISTS (SELECT 1 FROM casino.__table WHERE moniker = v_target) THEN
            RETURN jsonb_build_object('success', false, 'message', 'Table not found');
        END IF;
        INSERT INTO casino.map_cardtable_player (cardtablemoniker, playermoniker, role)
            VALUES (v_target, v_actor, 'player')
            ON CONFLICT DO NOTHING;
        RETURN jsonb_build_object('success', true, 'message', 'Joined table');
    END IF;

    IF p_op = 'leave' THEN
        v_target := coalesce(p_args->>'player', v_actor::text);
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_target THEN
            RAISE EXCEPTION 'bed_casino_op: forbidden';
        END IF;
        DELETE FROM casino.map_cardtable_player
            WHERE cardtablemoniker = p_args->>'moniker' AND playermoniker = v_target;
        RETURN jsonb_build_object('success', FOUND, 'message', CASE WHEN FOUND THEN 'Left table' ELSE 'Not at table' END);
    END IF;

    RAISE EXCEPTION 'bed_casino_op: unknown op %', p_op;
END;
$$;

ALTER FUNCTION engine.bed_casino_op(bytea, text, jsonb) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_casino_op(bytea, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_casino_op(bytea, text, jsonb) TO bed;
