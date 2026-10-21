-- bed/sql/bed_group_functions.sql
-- Message group management for bed. Each function takes the SHA-256 digest of
-- the bearer token and reads the acting moniker from engine.__bed_token, so
-- bed cannot act as another member. Zoid6-owned, SECURITY DEFINER, fixed
-- search_path. bed holds EXECUTE only.
--
-- Rules:
--   create:            any live session; the creator becomes the first member.
--   add / remove:      sysop, or a current member of that group.
--   delete group:      sysop, or the group's creator.
--
-- Installed by ``bed --install`` as the owner. Idempotent.

CREATE OR REPLACE FUNCTION engine.bed_group_create(p_token_hash bytea, p_name text, p_description text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_id bigint;
BEGIN
    SELECT t.moniker INTO v_actor FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_group_create: no live session for this token';
    END IF;
    IF p_name IS NULL OR btrim(p_name) = '' THEN
        RAISE EXCEPTION 'bed_group_create: group name is required';
    END IF;
    IF lower(p_name) IN ('everyone', 'here') THEN
        RAISE EXCEPTION 'bed_group_create: group name % is reserved', p_name;
    END IF;

    INSERT INTO engine.__message_group (name, description, createdby)
    VALUES (p_name, p_description, v_actor)
    ON CONFLICT (name) DO NOTHING
    RETURNING id INTO v_id;

    IF v_id IS NULL THEN
        RAISE EXCEPTION 'bed_group_create: group % already exists', p_name;
    END IF;

    INSERT INTO engine.__message_group_member (group_id, member_moniker, addedby)
    VALUES (v_id, v_actor, v_actor)
    ON CONFLICT (group_id, member_moniker) DO NOTHING;

    RETURN v_id;
END
$$;

CREATE OR REPLACE FUNCTION engine.bed_group_add_member(p_token_hash bytea, p_name text, p_member citext)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_group_id bigint;
BEGIN
    SELECT t.moniker INTO v_actor FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_group_add_member: no live session for this token';
    END IF;

    SELECT g.id INTO v_group_id FROM engine.__message_group g WHERE g.name = p_name;
    IF v_group_id IS NULL THEN
        RAISE EXCEPTION 'bed_group_add_member: unknown group %', p_name;
    END IF;

    IF NOT (engine.checkmemberflag(v_actor, 'sysop') IS TRUE)
       AND NOT EXISTS (
           SELECT 1 FROM engine.__message_group_member gm
           WHERE gm.group_id = v_group_id AND gm.member_moniker = v_actor
       ) THEN
        RAISE EXCEPTION 'bed_group_add_member: only members of % (or sysops) may add members', p_name;
    END IF;

    INSERT INTO engine.__message_group_member (group_id, member_moniker, addedby)
    VALUES (v_group_id, p_member, v_actor)
    ON CONFLICT (group_id, member_moniker) DO NOTHING;

    RETURN TRUE;
END
$$;

CREATE OR REPLACE FUNCTION engine.bed_group_remove_member(p_token_hash bytea, p_name text, p_member citext)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_group_id bigint;
    v_removed boolean;
BEGIN
    SELECT t.moniker INTO v_actor FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_group_remove_member: no live session for this token';
    END IF;

    SELECT g.id INTO v_group_id FROM engine.__message_group g WHERE g.name = p_name;
    IF v_group_id IS NULL THEN
        RETURN FALSE;
    END IF;

    IF NOT (engine.checkmemberflag(v_actor, 'sysop') IS TRUE)
       AND NOT EXISTS (
           SELECT 1 FROM engine.__message_group_member gm
           WHERE gm.group_id = v_group_id AND gm.member_moniker = v_actor
       ) THEN
        RAISE EXCEPTION 'bed_group_remove_member: only members of % (or sysops) may remove members', p_name;
    END IF;

    DELETE FROM engine.__message_group_member
    WHERE group_id = v_group_id AND member_moniker = p_member;
    GET DIAGNOSTICS v_removed = ROW_COUNT;
    RETURN v_removed;
END
$$;

CREATE OR REPLACE FUNCTION engine.bed_group_delete(p_token_hash bytea, p_name text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = engine, pg_temp
AS $$
DECLARE
    v_actor citext;
    v_creator citext;
    v_group_id bigint;
BEGIN
    SELECT t.moniker INTO v_actor FROM engine.__bed_token t
    WHERE t.token_hash = p_token_hash AND t.expires_at > now();
    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'bed_group_delete: no live session for this token';
    END IF;

    SELECT g.id, g.createdby INTO v_group_id, v_creator
    FROM engine.__message_group g WHERE g.name = p_name;
    IF v_group_id IS NULL THEN
        RETURN FALSE;
    END IF;

    IF NOT (engine.checkmemberflag(v_actor, 'sysop') IS TRUE) AND v_creator IS DISTINCT FROM v_actor THEN
        RAISE EXCEPTION 'bed_group_delete: only sysops or the group creator may delete %', p_name;
    END IF;

    DELETE FROM engine.__message_group WHERE id = v_group_id;
    RETURN TRUE;
END
$$;

ALTER FUNCTION engine.bed_group_create(bytea, text, text) OWNER TO zoid6;
ALTER FUNCTION engine.bed_group_add_member(bytea, text, citext) OWNER TO zoid6;
ALTER FUNCTION engine.bed_group_remove_member(bytea, text, citext) OWNER TO zoid6;
ALTER FUNCTION engine.bed_group_delete(bytea, text) OWNER TO zoid6;

REVOKE ALL ON FUNCTION engine.bed_group_create(bytea, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.bed_group_add_member(bytea, text, citext) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.bed_group_remove_member(bytea, text, citext) FROM PUBLIC;
REVOKE ALL ON FUNCTION engine.bed_group_delete(bytea, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION engine.bed_group_create(bytea, text, text) TO bed;
GRANT EXECUTE ON FUNCTION engine.bed_group_add_member(bytea, text, citext) TO bed;
GRANT EXECUTE ON FUNCTION engine.bed_group_remove_member(bytea, text, citext) TO bed;
GRANT EXECUTE ON FUNCTION engine.bed_group_delete(bytea, text) TO bed;
