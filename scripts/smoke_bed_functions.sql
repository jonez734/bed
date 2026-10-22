-- scripts/smoke_bed_functions.sql
-- Live smoke test of bed's session-bound functions, run with bed's privileges.
--
-- Run as jam (owner). The test runs in one DO block. Each bed call is made
-- after SET LOCAL ROLE bed, so it proves what bed can do, not what jam can do.
-- The token digest never leaves the database, so no bytea goes through psql.
--
--   psql -h /var/run/postgresql -U jam -d zoid6 -v ON_ERROR_STOP=1 \
--        -f scripts/smoke_bed_functions.sql
--
-- Writes only rows it creates: one token row, one group, one message. It
-- removes them whether the checks pass or fail. Real members and groups are
-- not touched.
--
-- Output: a NOTICE "PASS: ..." per check. A failed check raises an exception
-- and the script stops.

DO $smoke$
DECLARE
    v_tok    text := 'smoke-' || substr(md5(random()::text), 1, 12);
    v_hash   bytea := digest(convert_to(v_tok, 'UTF8'), 'sha256');
    v_bad    bytea := digest(convert_to('not-a-live-token', 'UTF8'), 'sha256');
    v_grp    text := 'smoke_' || substr(md5(random()::text), 1, 8);
    v_msg    bigint;
    v_ok     boolean;
    v_unread bigint;
BEGIN
    -- Write the token row through the owner function, as bed.
    SET LOCAL ROLE bed;
    PERFORM engine.bed_token_put(v_hash, 'jam'::citext, 'smoke-session',
        extract(epoch FROM now())::double precision,
        extract(epoch FROM now() + interval '10 minutes')::double precision,
        false, 'smoke-instance', 'smoke-ws', '{}'::jsonb, NULL);

    -- 1. A digest with no live session is refused.
    BEGIN
        PERFORM engine.bed_group_create(v_bad, 'nope', NULL);
        RAISE EXCEPTION 'FAIL: a bad token was accepted';
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM NOT LIKE '%no live session%' THEN
            RAISE;
        END IF;
    END;
    RAISE NOTICE 'PASS: bad token refused';

    -- 2. Group create. The creator becomes the first member.
    PERFORM engine.bed_group_create(v_hash, v_grp, 'smoke test group');
    RAISE NOTICE 'PASS: group created';

    -- 3. Add then remove a member. jam is the creator, so the membership rule
    --    passes for the acting session.
    v_ok := engine.bed_group_add_member(v_hash, v_grp, 'jam'::citext);
    IF NOT v_ok THEN RAISE EXCEPTION 'FAIL: add member refused'; END IF;
    RAISE NOTICE 'PASS: member add accepted';

    v_ok := engine.bed_group_remove_member(v_hash, v_grp, 'jam'::citext);
    IF NOT v_ok THEN RAISE EXCEPTION 'FAIL: remove member did not remove'; END IF;
    RAISE NOTICE 'PASS: member remove accepted';

    -- Re-add so the creator stays a member for the delete check.
    PERFORM engine.bed_group_add_member(v_hash, v_grp, 'jam'::citext);

    -- 4. Direct send to jam, then the unread count sees it.
    v_msg := engine.bed_message_send(v_hash, 'smoke.bed', 'smoke test message',
        'ROUTINE', ARRAY['jam']::citext[]);
    IF v_msg IS NULL OR v_msg <= 0 THEN RAISE EXCEPTION 'FAIL: send returned no id'; END IF;
    RAISE NOTICE 'PASS: direct send stored (id %)', v_msg;

    v_unread := engine.bed_count_unread('jam'::citext);
    IF v_unread < 1 THEN RAISE EXCEPTION 'FAIL: unread count did not include the message'; END IF;
    RAISE NOTICE 'PASS: unread count includes the smoke message (%)', v_unread;

    -- 5. The creator deletes the group.
    v_ok := engine.bed_group_delete(v_hash, v_grp);
    IF NOT v_ok THEN RAISE EXCEPTION 'FAIL: group delete did not delete'; END IF;
    RAISE NOTICE 'PASS: group deleted by creator';

    RESET ROLE;

    -- Cleanup as the owner: remove only the rows this run created.
    DELETE FROM engine.__message_recipient WHERE message_id = v_msg;
    DELETE FROM engine.__message WHERE id = v_msg;
    DELETE FROM engine.__bed_token WHERE token_hash = v_hash;
    RAISE NOTICE 'DONE: smoke test complete, test rows removed';
EXCEPTION WHEN OTHERS THEN
    -- Remove the token row even on failure, then re-raise so the failure shows.
    RESET ROLE;
    DELETE FROM engine.__bed_token WHERE token_hash = v_hash;
    RAISE;
END
$smoke$;
