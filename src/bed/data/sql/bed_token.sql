-- bed/data/sql/bed_token.sql
-- Schema for the optional --token-persistence=db backend of bed's
-- AuthService bearer tokens. Applied lazily by DBTokenStore the first
-- time a token is written, so a fresh install does not require a
-- separate migration step.

CREATE TABLE IF NOT EXISTS engine.__bed_token (
    token             text        PRIMARY KEY,
    moniker           citext      NOT NULL,
    session_id        text        NOT NULL,
    issued_at         timestamptz NOT NULL,
    expires_at        timestamptz NOT NULL,
    is_sysop          boolean     NOT NULL DEFAULT false,
    bed_instance_id   text        NOT NULL,
    websocket_id      text        NOT NULL,
    claims            jsonb       NOT NULL,
    loginid           text        NULL
);

CREATE INDEX IF NOT EXISTS __bed_token_expires_at_idx
    ON engine.__bed_token (expires_at);

CREATE INDEX IF NOT EXISTS __bed_token_session_id_idx
    ON engine.__bed_token (session_id);
