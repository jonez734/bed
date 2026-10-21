-- bed/sql/bed_token.sql
-- Bearer-token store for bed (token_persistence = 'db').
--
-- Installed by ``bed --install`` as the owner (zoid6). Not created at runtime.
-- The raw bearer token is never stored: only its SHA-256 digest (token_hash).
-- Expired rows are removed by bed's periodic cleanup (gc_expired).
-- bed holds no table privileges here; access goes through the zoid6-owned
-- functions in bed_token_functions.sql.

CREATE TABLE IF NOT EXISTS engine.__bed_token (
    token_hash        bytea       PRIMARY KEY,
    moniker           citext      NOT NULL,
    session_id        text        NOT NULL,
    issued_at         timestamptz NOT NULL,
    expires_at        timestamptz NOT NULL,
    is_sysop          boolean     NOT NULL DEFAULT false,
    bed_instance_id   text        NOT NULL,
    websocket_id      text        NOT NULL,
    claims            jsonb       NOT NULL,
    loginid           text        NULL,
    CONSTRAINT chk_bed_token_hash_len CHECK (octet_length(token_hash) = 32)
);

CREATE INDEX IF NOT EXISTS __bed_token_expires_at_idx
    ON engine.__bed_token (expires_at);

CREATE INDEX IF NOT EXISTS __bed_token_session_id_idx
    ON engine.__bed_token (session_id);

ALTER TABLE engine.__bed_token OWNER TO zoid6;

REVOKE ALL ON engine.__bed_token FROM PUBLIC;
