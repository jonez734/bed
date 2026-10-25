-- bed/sql/bed_bank_op_functions.sql
-- One SECURITY DEFINER function for the bank operations bed serves, dispatched by op name, the
-- way bbsengine6.module.access() dispatches by op. bed holds no table privileges on the bank
-- tables; it calls engine.bed_bank_op with the SHA-256 digest of the bearer token.
--
-- The acting moniker and sysop status come from the verified token row (engine.__bed_token),
-- never from the request. Each op checks its own rule before it reads or writes:
--   add, remove:          the target account must be the caller's own, or the caller is a sysop.
--   history:              the target must be the caller's own, or the caller is a sysop.
--   list_all:             sysop only.
--   pending:              the caller's own pending transfers, or all of them for a sysop.
--   transfer_request:     the caller must be the sender (from_moniker), or a sysop.
--   transfer_approve:     the caller must be the recipient, or a sysop.
--   transfer_reject:      the caller must be the recipient, or a sysop.
-- An unknown op is refused. Each call returns one jsonb result with success and the op's data.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_bank_op(p_token_hash bytea, p_op text, p_args jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, public, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_sysop boolean;
    v_moniker citext;
    v_amount numeric;
    v_limit int;
    v_id bigint;
    v_acct bigint;
    v_balance numeric;
    v_rows jsonb;
    v_from citext;
    v_to citext;
    v_status text;
BEGIN
    SELECT t.moniker INTO v_actor
    FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_bank_op: no live session for this token';
    END IF;
    v_sysop := engine.bed_is_sysop(v_actor) IS TRUE;

    IF p_op = 'add' OR p_op = 'remove' THEN
        v_moniker := p_args->>'moniker';
        v_amount := (p_args->>'amount')::numeric;
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_moniker THEN
            RAISE EXCEPTION 'bed_bank_op: forbidden';
        END IF;
        IF v_amount IS NULL OR v_amount <= 0 THEN
            RETURN jsonb_build_object('success', false, 'code', 'invalid_amount',
                                      'message', 'Amount must be positive');
        END IF;
        IF p_op = 'add' THEN
            INSERT INTO bank.__account (moniker, balance) VALUES (v_moniker, 0)
                ON CONFLICT (moniker) DO NOTHING;
            UPDATE bank.__account SET balance = balance + v_amount
             WHERE moniker = v_moniker RETURNING id, balance INTO v_acct, v_balance;
            INSERT INTO bank.__transaction (accountid, amount, transactiontype, description, membermoniker)
                VALUES (v_acct, v_amount, 'deposit', COALESCE(p_args->>'description', ''), v_actor);
        ELSE
            UPDATE bank.__account SET balance = balance - v_amount
             WHERE moniker = v_moniker AND balance >= v_amount
            RETURNING id, balance INTO v_acct, v_balance;
            IF NOT FOUND THEN
                RETURN jsonb_build_object('success', false, 'code', 'insufficient_funds',
                                          'message', 'Insufficient balance');
            END IF;
            INSERT INTO bank.__transaction (accountid, amount, transactiontype, description, membermoniker)
                VALUES (v_acct, -v_amount, 'withdrawal', COALESCE(p_args->>'description', ''), v_actor);
        END IF;
        RETURN jsonb_build_object('success', true, 'moniker', v_moniker, 'new_balance', v_balance);

    ELSIF p_op = 'history' THEN
        v_moniker := p_args->>'moniker';
        v_limit := COALESCE((p_args->>'limit')::int, 50);
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_moniker THEN
            RAISE EXCEPTION 'bed_bank_op: forbidden';
        END IF;
        SELECT COALESCE(jsonb_agg(h), '[]'::jsonb) INTO v_rows FROM (
            SELECT t.id, t.amount, t.transactiontype, t.description, t.relatedmoniker, t.dateposted
              FROM bank.__transaction t
              JOIN bank.__account a ON a.id = t.accountid
             WHERE a.moniker = v_moniker
             ORDER BY t.dateposted DESC, t.id DESC
             LIMIT greatest(v_limit, 0)
        ) h;
        RETURN jsonb_build_object('success', true, 'rows', v_rows);

    ELSIF p_op = 'list_all' THEN
        IF NOT v_sysop THEN
            RAISE EXCEPTION 'bed_bank_op: forbidden';
        END IF;
        SELECT COALESCE(jsonb_agg(a), '[]'::jsonb) INTO v_rows FROM (
            SELECT moniker, balance FROM bank.__account ORDER BY moniker
        ) a;
        RETURN jsonb_build_object('success', true, 'rows', v_rows);

    ELSIF p_op = 'pending' THEN
        v_moniker := p_args->>'moniker';
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_moniker THEN
            RAISE EXCEPTION 'bed_bank_op: forbidden';
        END IF;
        SELECT COALESCE(jsonb_agg(p), '[]'::jsonb) INTO v_rows FROM (
            SELECT tr.id, tr.fromaccountid, tr.toaccountid, tr.amount, tr.status, tr.requestedby
              FROM bank.__transfer tr
             WHERE tr.status = 'pending'
               AND (v_sysop OR tr.requestedby = v_actor)
             ORDER BY tr.id
        ) p;
        RETURN jsonb_build_object('success', true, 'rows', v_rows);

    ELSIF p_op = 'transfer_request' THEN
        v_from := p_args->>'from_moniker';
        v_to := p_args->>'to_moniker';
        v_amount := (p_args->>'amount')::numeric;
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_from THEN
            RAISE EXCEPTION 'bed_bank_op: forbidden';
        END IF;
        IF v_amount IS NULL OR v_amount <= 0 THEN
            RETURN jsonb_build_object('success', false, 'code', 'invalid_amount',
                                      'message', 'Amount must be positive');
        END IF;
        INSERT INTO bank.__transfer (fromaccountid, toaccountid, amount, status, requestedby)
            SELECT af.id, at.id, v_amount, 'pending', v_actor
              FROM bank.__account af, bank.__account at
             WHERE af.moniker = v_from AND at.moniker = v_to
        RETURNING id INTO v_id;
        IF v_id IS NULL THEN
            RETURN jsonb_build_object('success', false, 'code', 'no_such_account',
                                      'message', 'Sender or recipient has no account');
        END IF;
        RETURN jsonb_build_object('success', true, 'transfer_id', v_id, 'message', 'queued');

    ELSIF p_op = 'transfer_approve' OR p_op = 'transfer_reject' THEN
        v_id := (p_args->>'transfer_id')::bigint;
        SELECT a.moniker INTO v_to FROM bank.__transfer tr
          JOIN bank.__account a ON a.id = tr.toaccountid WHERE tr.id = v_id;
        IF NOT v_sysop AND v_actor IS DISTINCT FROM v_to THEN
            RAISE EXCEPTION 'bed_bank_op: forbidden';
        END IF;
        v_status := CASE WHEN p_op = 'transfer_approve' THEN 'approved' ELSE 'rejected' END;
        UPDATE bank.__transfer SET status = v_status, respondedby = v_actor
         WHERE id = v_id AND status = 'pending';
        IF NOT FOUND THEN
            RETURN jsonb_build_object('success', false, 'code', 'not_pending',
                                      'message', 'Transfer is not pending');
        END IF;
        RETURN jsonb_build_object('success', true, 'transfer_id', v_id, 'status', v_status);

    END IF;

    RAISE EXCEPTION 'bed_bank_op: unknown op %', p_op;
END
$$;

ALTER FUNCTION engine.bed_bank_op(bytea, text, jsonb) OWNER TO zoid6;
REVOKE ALL ON FUNCTION engine.bed_bank_op(bytea, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine.bed_bank_op(bytea, text, jsonb) TO bed;
