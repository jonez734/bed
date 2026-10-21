# bed/store/token_store.py
# Reusable hashed bearer-token store on engine.__bed_token.
#
# Standalone: no import of the auth service, the daemon, or any role name.
# The raw token is hashed (SHA-256) before it reaches SQL. Only the digest
# is stored. Expiry is enforced in SQL (expires_at > now()).

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

TABLE = "engine.__bed_token"


def token_hash(token: str) -> bytes:
    """SHA-256 digest of a bearer token. The only form stored or queried."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def put_sql() -> str:
    return f"""
        INSERT INTO {TABLE}
            (token_hash, moniker, session_id, issued_at, expires_at,
             is_sysop, bed_instance_id, websocket_id, claims, loginid)
        VALUES (%s, %s, %s, to_timestamp(%s), to_timestamp(%s),
                %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (token_hash) DO UPDATE SET
            moniker = EXCLUDED.moniker,
            session_id = EXCLUDED.session_id,
            issued_at = EXCLUDED.issued_at,
            expires_at = EXCLUDED.expires_at,
            is_sysop = EXCLUDED.is_sysop,
            bed_instance_id = EXCLUDED.bed_instance_id,
            websocket_id = EXCLUDED.websocket_id,
            claims = EXCLUDED.claims,
            loginid = EXCLUDED.loginid
    """


def put_params(record: Any) -> tuple:
    return (
        token_hash(record.token),
        record.moniker,
        record.session_id,
        record.issued_at,
        record.expires_at,
        record.is_sysop,
        record.bed_instance_id,
        record.websocket_id,
        json.dumps(record.claims),
        record.loginid,
    )


def get_sql() -> str:
    return f"""
        SELECT moniker, session_id,
               EXTRACT(EPOCH FROM issued_at)::double precision AS issued_at,
               EXTRACT(EPOCH FROM expires_at)::double precision AS expires_at,
               is_sysop, bed_instance_id, websocket_id, claims, loginid
        FROM {TABLE}
        WHERE token_hash = %s AND expires_at > now()
    """


def get_params(token: str) -> tuple:
    return (token_hash(token),)


def delete_sql() -> str:
    return f"DELETE FROM {TABLE} WHERE token_hash = %s"


def delete_params(token: str) -> tuple:
    return (token_hash(token),)


def gc_sql(now: Optional[float]) -> tuple:
    if now is None:
        return f"DELETE FROM {TABLE} WHERE expires_at <= now()", ()
    return f"DELETE FROM {TABLE} WHERE expires_at <= to_timestamp(%s)", (float(now),)
