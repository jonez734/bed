# bed/api/token_store.py
# Bearer-token storage for bed.

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol


@dataclass
class TokenRecord:
    """The state held for one issued bearer token.

    `claims` is the full original JSON-claims dict, preserved verbatim so the
    auth service can re-encode / verify on round trips. The other fields are
    promoted to the top level because every code path that uses them
    (reconnect rebind, session lookup, pending-request replay) needs the
    same hot path, and a dict indirection is wasteful.
    """
    token: str
    moniker: str
    session_id: str
    issued_at: float
    expires_at: float
    is_sysop: bool
    bed_instance_id: str
    websocket_id: str
    claims: Dict[str, Any] = field(default_factory=dict)
    loginid: Optional[str] = None


@dataclass
class MemberInfo:
    """Return value of a successful CredentialProvider.authenticate() call."""
    moniker: str
    is_sysop: bool = False
    balance: Optional[int] = None
    loginid: Optional[str] = None


class TokenStore(Protocol):
    """Pluggable backend for issued bearer tokens.

    The in-process store is the v1 default. The DB store is opt-in via
    --token-persistence=db. All implementations must be safe to call from
    multiple coroutines (auth runs in the WebSocket dispatch loop).
    """

    def put(self, record: TokenRecord) -> None: ...
    def get(self, token: str) -> Optional[TokenRecord]: ...
    def delete(self, token: str) -> bool: ...
    def gc_expired(self, now: Optional[float] = None) -> int: ...


def _record_token_hash(token: str) -> str:
    """8-char SHA-256 prefix for a token. See ``bed.api.auth._token_hash``
    for the rationale (correlate across log lines without leaking the
    bearer secret). The two helpers are intentionally identical so a
    single ``tok=`` field can be diffed across the auth.py and
    token_store.py debug output.
    """
    if not token:
        return "<empty>"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


class InMemoryTokenStore:
    """In-process Dict[token, TokenRecord], guarded by a threading.Lock.

    Expiry is enforced at `get` time (lazy). `gc_expired` is exposed for
    parity with the DB backend but is a no-op: the in-process store has
    bounded size by construction (one record per live connection).

    Optional `now_factory` lets a test inject a fake clock so expiry can
    be triggered deterministically. Production code should leave it None.

    Mutation logging: every mutating method emits one ``io.echo`` line
    tagged ``InMemoryTokenStore.debug`` so an operator reading a
    ``token_revoked`` reply can grep the bed log for the exact mutation
    sequence (put / get_miss / get_hit / get_lazy_gc / delete /
    gc_expired) and read off which path produced the failure. No level
    kwarg -- bbsengine6's ``io.echo`` defaults surface this in the
    operator's normal log stream.
    """

    def __init__(
        self,
        now_factory: Optional[Any] = None,
        *,
        debug: bool = False,
    ) -> None:
        self._records: Dict[str, TokenRecord] = {}
        self._lock = threading.Lock()
        self._now_factory = now_factory
        # Kept on the constructor for API parity with earlier
        # revisions of this module; not used to gate logging any
        # more (the operator wants the lines unconditionally).
        self._debug_enabled = bool(debug)

    def _now(self) -> float:
        if self._now_factory is not None:
            return float(self._now_factory())
        return _now_ts()

    def _dbg(self, op: str, token: str = "", **extra: Any) -> None:
        """Emit one log line. No level kwarg -- see class docstring."""
        from bbsengine6 import io
        fields = [
            f"op={op}",
            f"size={len(self._records)}",
        ]
        if token:
            fields.append(f"tok={_record_token_hash(token)}")
        for k, v in extra.items():
            fields.append(f"{k}={v}")
        io.echo("InMemoryTokenStore.debug: " + " ".join(fields))

    def put(self, record: TokenRecord) -> None:
        with self._lock:
            existed = record.token in self._records
            self._records[record.token] = record
            self._dbg(
                "put",
                token=record.token,
                **{"overwrote": existed, "expires_at": record.expires_at},
            )

    def get(self, token: str) -> Optional[TokenRecord]:
        with self._lock:
            rec = self._records.get(token)
            if rec is None:
                self._dbg("get_miss", token=token)
                return None
            now_ts = self._now()
            if rec.expires_at <= now_ts:
                self._records.pop(token, None)
                self._dbg(
                    "get_lazy_gc",
                    token=token,
                    **{"expires_at": rec.expires_at, "now": now_ts},
                )
                return None
            self._dbg("get_hit", token=token)
            return rec

    def delete(self, token: str) -> bool:
        with self._lock:
            present = self._records.pop(token, None) is not None
            self._dbg("delete", token=token, **{"present": present})
            return present

    def gc_expired(self, now: Optional[float] = None) -> int:
        if now is None:
            now = self._now()
        with self._lock:
            expired = [t for t, r in self._records.items() if r.expires_at <= now]
            for t in expired:
                self._records.pop(t, None)
            self._dbg(
                "gc_expired",
                **{"count": len(expired), "now": now},
            )
        return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def __bool__(self) -> bool:
        return True


class DBTokenStore:
    """PostgreSQL-backed token store.

    Schema: see bed/src/bed/data/sql/bed_token.sql. The store owns no
    connection: it borrows one from the bbsengine6 connection pool each
    call. This matches the pattern used throughout bbsengine6.member /
    bbsengine6.bank.
    """

    TABLE = "engine.__bed_token"

    def __init__(self, args: Any) -> None:
        self._args = args

    def _pool(self) -> Any:
        pool = getattr(self._args, "pool", None)
        if pool is None:
            from bbsengine6.database import getpool

            pool = getpool(self._args)
            self._args.pool = pool
        return pool

    def put(self, record: TokenRecord) -> None:
        from bbsengine6.database import connect, cursor as _cursor

        pool = self._pool()
        with connect(self._args, pool=pool) as conn:
            with _cursor(conn) as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.TABLE}
                        (token, moniker, session_id, issued_at, expires_at,
                         is_sysop, bed_instance_id, websocket_id, claims,
                         loginid)
                    VALUES (%s, %s, %s, to_timestamp(%s), to_timestamp(%s),
                            %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (token) DO UPDATE SET
                        moniker = EXCLUDED.moniker,
                        session_id = EXCLUDED.session_id,
                        issued_at = EXCLUDED.issued_at,
                        expires_at = EXCLUDED.expires_at,
                        is_sysop = EXCLUDED.is_sysop,
                        bed_instance_id = EXCLUDED.bed_instance_id,
                        websocket_id = EXCLUDED.websocket_id,
                        claims = EXCLUDED.claims,
                        loginid = EXCLUDED.loginid
                    """,
                    (
                        record.token,
                        record.moniker,
                        record.session_id,
                        record.issued_at,
                        record.expires_at,
                        record.is_sysop,
                        record.bed_instance_id,
                        record.websocket_id,
                        json.dumps(record.claims),
                        record.loginid,
                    ),
                )

    def get(self, token: str) -> Optional[TokenRecord]:
        from bbsengine6.database import connect, cursor as _cursor

        pool = self._pool()
        with connect(self._args, pool=pool) as conn:
            with _cursor(conn) as cur:
                cur.execute(
                    f"""
                    SELECT token, moniker, session_id,
                           EXTRACT(EPOCH FROM issued_at)::double precision AS issued_at,
                           EXTRACT(EPOCH FROM expires_at)::double precision AS expires_at,
                           is_sysop, bed_instance_id, websocket_id, claims,
                           loginid
                    FROM {self.TABLE}
                    WHERE token = %s AND expires_at > now()
                    """,
                    (token,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        claims = row.get("claims")
        if isinstance(claims, str):
            try:
                claims = json.loads(claims)
            except json.JSONDecodeError:
                claims = {}
        elif not isinstance(claims, dict):
            claims = {}
        return TokenRecord(
            token=row["token"],
            moniker=row["moniker"],
            session_id=row["session_id"],
            issued_at=float(row["issued_at"]),
            expires_at=float(row["expires_at"]),
            is_sysop=bool(row["is_sysop"]),
            bed_instance_id=row["bed_instance_id"],
            websocket_id=row["websocket_id"],
            claims=claims,
            loginid=row.get("loginid"),
        )

    def delete(self, token: str) -> bool:
        from bbsengine6.database import connect, cursor as _cursor

        pool = self._pool()
        with connect(self._args, pool=pool) as conn:
            with _cursor(conn) as cur:
                cur.execute(
                    f"DELETE FROM {self.TABLE} WHERE token = %s",
                    (token,),
                )
                return cur.rowcount > 0

    def gc_expired(self, now: Optional[float] = None) -> int:
        from bbsengine6.database import connect, cursor as _cursor

        pool = self._pool()
        if now is None:
            cur_sql = f"DELETE FROM {self.TABLE} WHERE expires_at <= now()"
            params: tuple = ()
        else:
            cur_sql = f"DELETE FROM {self.TABLE} WHERE expires_at <= to_timestamp(%s)"
            params = (float(now),)
        with connect(self._args, pool=pool) as conn:
            with _cursor(conn) as cur:
                cur.execute(cur_sql, params)
                return cur.rowcount


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()
