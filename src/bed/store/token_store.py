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
    return "SELECT engine.bed_token_put(%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"


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
    return "SELECT * FROM engine.bed_token_get(%s)"


def get_params(token: str) -> tuple:
    return (token_hash(token),)


def delete_sql() -> str:
    return "SELECT engine.bed_token_delete(%s)"


def delete_params(token: str) -> tuple:
    return (token_hash(token),)


def gc_sql(now: Optional[float]) -> tuple:
    """Call the zoid6-owned gc function. ``now`` None means the database clock."""
    return "SELECT engine.bed_token_gc(%s)", (None if now is None else float(now),)
