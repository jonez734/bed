# bed/api/auth.py
# AuthService: short-lived signed bearer tokens for bed.
#
# Authentication / authorization:
#   Every handler delegates its per-op policy decision to
#   ``bbsengine6.auth.access(args, op, session=live_state, message=msg)``.
#   The bbsengine6.auth package owns the op vocabulary ("login",
#   "reconnect", "refresh", "revoke") and the per-op policy; this module
#   is the bed-side consumer, parallel to bed/api/bank.py for bank.
#
#   Handlers perform two gates in order:
#     1. Wire-shape validation -- token decode + signature verify
#        + expiry + instance match + store presence (returns the
#        existing per-op error codes: token_invalid, token_expired,
#        instance_mismatch, token_revoked). This stays in the handler
#        because it touches bed's HMAC scheme.
#     2. ``bbsengine6.auth.access()`` policy decision (else forbidden
#        for reconnect/revoke; else not_authenticated for refresh so
#        the client can recover via reconnect). ``login`` always
#        returns True -- the credential provider decides.

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bbsengine6 import io
from bbsengine6.auth import access as _auth_access

from .credential_provider import CredentialProvider
from .errors import (
    CODE_BAD_CREDENTIALS,
    CODE_FORBIDDEN,
    CODE_INSTANCE_MISMATCH,
    CODE_MISSING_CREDENTIALS,
    CODE_NOT_AUTHENTICATED,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    CODE_TOKEN_REVOKED,
    error_envelope,
    forbidden,
    scrub_token,
)
from .handler import BaseService
from .session import SessionRegistry, SessionState
from .token_store import MemberInfo, TokenRecord, TokenStore


class TokenError(Exception):
    """Raised by the token codec on any decode/verify failure.

    The `code` is the wire-protocol error code that AuthService should
    surface to the client.
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


TOKEN_CLAIM_VERSION = 1
SUPPORTED_TOKEN_VERSIONS = frozenset({TOKEN_CLAIM_VERSION})


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _now_iso() -> str:
    return _iso_from_ts(_now_ts())


def _iso_from_ts(ts: float) -> str:
    """Render a UNIX timestamp as an ISO-8601 UTC string with ``Z`` suffix."""
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _token_hash(token: str) -> str:
    """Stable short identifier for a token, used in debug logs only.

    Real tokens are never logged; the SHA-256 prefix (8 chars) is
    enough to correlate "same token" across log lines without leaking
    enough material to forge a request. Tokens shorter than 8 chars
    produce fewer characters (no padding) -- still safe to log.
    """
    if not token:
        return "<empty>"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def _store_size(token_store: Any) -> int:
    """Best-effort store size for debug logs. Falls back to ``-1``
    when the backend is the DB-backed store (the COUNT would be
    intrusive to run for every debug line) or any other custom
    implementation that does not expose ``__len__``.
    """
    try:
        return len(token_store)
    except TypeError:
        return -1


def _debug_branch(
    args: Any,
    *,
    op: str,
    branch: str,
    token: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit one log line identifying which branch an auth handler
    took.

    The ``branch`` token is a short, greppable label (e.g.
    ``EXPIRED``, ``REVOKED``, ``INSTANCE_MISMATCH``, ``OK``,
    ``GARBLED``) chosen to be unique in this module so a single
    grep on the bed log pins down the failing path.

    ``token`` is hashed before logging (see :func:`_token_hash`) so
    an operator reading the log can correlate "this token was on
    the wire" across lines without recovering the bearer secret.

    No level kwarg: bbsengine6's ``io.echo`` defaults are loud
    enough to surface this in the operator's normal log stream.
    """
    fields = [f"op={op}", f"branch={branch}", f"tok={_token_hash(token)}"]
    if extra:
        for k, v in extra.items():
            fields.append(f"{k}={v}")
    io.echo("AuthService.debug: " + " ".join(fields))


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _encode_token(claims: Dict[str, Any], secret: bytes) -> str:
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64encode(payload)
    mac = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{mac}"


def _decode_token(token: str, secret: bytes) -> Dict[str, Any]:
    if not isinstance(token, str) or "." not in token:
        raise TokenError(CODE_TOKEN_INVALID, "malformed token")
    payload_b64, mac = token.rsplit(".", 1)
    if not payload_b64 or not mac:
        raise TokenError(CODE_TOKEN_INVALID, "malformed token")
    expected = hmac.new(
        secret, payload_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise TokenError(CODE_TOKEN_INVALID, "bad signature")
    try:
        claims = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise TokenError(CODE_TOKEN_INVALID, f"unparseable claims: {e}") from e
    if not isinstance(claims, dict):
        raise TokenError(CODE_TOKEN_INVALID, "claims not an object")
    version = claims.get("version")
    if not isinstance(version, int) or version not in SUPPORTED_TOKEN_VERSIONS:
        raise TokenError(
            CODE_TOKEN_INVALID,
            f"unsupported token version: {version!r}",
        )
    return claims


# Map from WS ``type`` field to the domain verb understood by
# ``bbsengine6.auth.access``. The auth module owns the verb vocabulary;
# this dict is the only place bed-side code needs to maintain the
# translation.
_TYPE_TO_OP: Dict[str, str] = {
    "auth": "login",
    "reconnect": "reconnect",
    "auth_refresh": "refresh",
    "auth_revoke": "revoke",
}


def _deny_envelope(op: str) -> Dict[str, Any]:
    """Translate an ``access()=False`` decision into the wire-protocol envelope.

    The choice of code preserves existing client semantics:
      - ``refresh`` denial -> ``not_authenticated`` (recoverable, client
        may try reconnect with its last good token).
      - ``reconnect`` / ``revoke`` denial -> ``forbidden`` (the request
        is structurally wrong for this session/token).
    ``login`` never denies in the current policy.
    """
    if op == "refresh":
        return error_envelope(
            CODE_NOT_AUTHENTICATED,
            "auth_refresh requires the original socket; use reconnect",
            recoverable=True,
        )
    return forbidden("Operation not permitted for this token")


class AuthService(BaseService):
    """Issue, validate, refresh, revoke bearer tokens; rebind on reconnect.

    The service registers four message types: `auth`, `reconnect`,
    `auth_refresh`, `auth_revoke`. The remaining `ping` / `list_services`
    types are left to the loaded game router.

    Token codec: <urlsafe-b64-payload>.<hex hmac-sha256>.
    Payload claims: moniker, issued_at, expires_at, session_id, is_sysop,
    bed_instance_id, websocket_id, plus any future additions (forward-
    compatible because the codec is JSON).
    """

    HANDLED_TYPES = ("auth", "reconnect", "auth_refresh", "auth_revoke")

    def __init__(
        self,
        args: Any,
        session_registry: SessionRegistry,
        token_store: TokenStore,
        credential_provider: CredentialProvider,
        secret: bytes,
        instance_id: str,
        ttl_seconds: int = 900,
        *,
        clock: Optional[Any] = None,
    ) -> None:
        from .handler import SessionManager

        super().__init__(args, SessionManager())
        self.sessions = session_registry
        self.token_store = token_store
        self.credential_provider = credential_provider
        self.secret = bytes(secret)
        self.instance_id = str(instance_id)
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._clock = clock
        self.server: Any = None

    def register_all(self, server: Any) -> None:
        self.server = server
        server.register_service(self, list(self.HANDLED_TYPES))

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        return _now_ts()

    def _authorize(
        self,
        op: str,
        claims: Dict[str, Any],
        live_state: Optional[SessionState],
    ) -> Optional[Dict[str, Any]]:
        """Delegate the per-op policy decision to ``bbsengine6.auth.access``.

        Returns ``None`` on allow, or an error envelope on deny.
        ``live_state`` is the SessionState currently bound to the
        websocket (or ``None`` if unbound). ``claims`` is the decoded
        token claims dict (or ``{}`` if the op doesn't need a token).
        """
        msg = {"claims": claims or {}}
        if _auth_access(self.args, op, session=live_state, message=msg):
            return None
        return _deny_envelope(op)

    def _mint_record(
        self,
        info: MemberInfo,
        session_id: str,
        websocket_id: str,
        *,
        now: Optional[float] = None,
    ) -> TokenRecord:
        ts = now if now is not None else self._now()
        claims = {
            "version": TOKEN_CLAIM_VERSION,
            "moniker": info.moniker,
            "issued_at": ts,
            "expires_at": ts + self.ttl_seconds,
            "session_id": session_id,
            "is_sysop": bool(info.is_sysop),
            "bed_instance_id": self.instance_id,
            "websocket_id": websocket_id,
            "loginid": info.loginid,
        }
        token = _encode_token(claims, self.secret)
        return TokenRecord(
            token=token,
            moniker=info.moniker,
            session_id=session_id,
            issued_at=ts,
            expires_at=ts + self.ttl_seconds,
            is_sysop=bool(info.is_sysop),
            bed_instance_id=self.instance_id,
            websocket_id=websocket_id,
            claims=claims,
            loginid=info.loginid,
        )

    def _persist(self, record: TokenRecord) -> None:
        try:
            self.token_store.put(record)
        except Exception as e:
            io.echo(
                f"AuthService: token_store.put failed for {scrub_token({'token': record.token})}: {e}",
                level="error",
            )
            raise

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        op = _TYPE_TO_OP.get(msg_type)
        if op is None:
            return None
        handler = _OP_TO_HANDLER[op]
        return await handler(self, websocket, message)

    async def _handle_auth(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        moniker = (message.get("moniker") or "").strip()
        password = message.get("password") or ""
        if not moniker or not password:
            _debug_branch(
                self.args,
                op="login",
                branch="MISSING_CREDENTIALS",
                token="",
            )
            return error_envelope(
                CODE_MISSING_CREDENTIALS,
                "moniker and password are required",
                recoverable=False,
            )

        err = self._authorize("login", {}, None)
        if err is not None:
            _debug_branch(
                self.args,
                op="login",
                branch="POLICY_DENY",
                token="",
            )
            return err

        info = self.credential_provider.authenticate(
            self.args, moniker, password, pool=getattr(self.args, "pool", None)
        )
        if info is None:
            _debug_branch(
                self.args,
                op="login",
                branch="BAD_CREDENTIALS",
                token="",
                extra={"moniker": moniker},
            )
            return error_envelope(
                CODE_BAD_CREDENTIALS,
                "Invalid moniker or password",
                recoverable=False,
            )

        session_id = str(uuid.uuid4())
        websocket_id = str(websocket.id)
        state = self.sessions.bind(
            session_id,
            websocket_id,
            info.moniker,
            info.is_sysop,
            balance=info.balance,
            loginid=info.loginid,
        )
        state.auth_service_token = None
        record = self._mint_record(info, session_id, websocket_id)
        self._persist(record)
        state.auth_service_token = record.token
        _debug_branch(
            self.args,
            op="login",
            branch="OK",
            token=record.token,
            extra={
                "moniker": info.moniker,
                "expires_at": record.expires_at,
                "store_size": _store_size(self.token_store),
            },
        )

        io.echo(
            f"AuthService: issued token for moniker={info.moniker!r} "
            f"loginid={info.loginid!r} session={session_id[:8]}…",
        )
        return self._auth_result_envelope(record, info, fresh=True)

    async def _handle_reconnect(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        token = message.get("token") or ""
        new_websocket_id = str(websocket.id)

        if not token:
            _debug_branch(self.args, op="reconnect", branch="EMPTY_TOKEN", token=token)
            return error_envelope(CODE_TOKEN_INVALID, "token required", recoverable=False)

        try:
            claims = _decode_token(token, self.secret)
        except TokenError as e:
            _debug_branch(
                self.args,
                op="reconnect",
                branch="GARBLED",
                token=token,
                extra={"code": e.code},
            )
            return error_envelope(e.code, str(e), recoverable=False)

        # Expiry is checked BEFORE the store lookup so a token whose
        # clock has run out surfaces as token_expired even when the
        # in-memory store's lazy-GC has already purged the record
        # (which would otherwise mask the expiry as token_revoked).
        # Mirrors casino/api/_auth.py and bed/api/message.py.
        claims_expires_at = float(claims.get("expires_at") or 0.0)
        now_at_expiry = self._now()
        if claims_expires_at <= now_at_expiry:
            _debug_branch(
                self.args,
                op="reconnect",
                branch="EXPIRED",
                token=token,
                extra={
                    "expires_at": claims_expires_at,
                    "now": now_at_expiry,
                    "delta": claims_expires_at - now_at_expiry,
                    "store_size": _store_size(self.token_store),
                },
            )
            try:
                self.token_store.delete(token)
            except Exception:
                pass
            existing = self.sessions.get_by_session(claims.get("session_id", ""))
            if existing is not None:
                existing.pending_request = None
            return error_envelope(
                CODE_TOKEN_EXPIRED,
                "Token has expired",
                recoverable=True,
            )

        store_record = self.token_store.get(token)
        if store_record is None:
            _debug_branch(
                self.args,
                op="reconnect",
                branch="REVOKED",
                token=token,
                extra={
                    "store_size": _store_size(self.token_store),
                    "session_id_prefix": (claims.get("session_id") or "")[:8],
                },
            )
            existing = self.sessions.get_by_session(claims.get("session_id", ""))
            if existing is not None:
                existing.pending_request = None
            return error_envelope(
                CODE_TOKEN_REVOKED,
                "Token is no longer valid",
                recoverable=False,
            )

        if store_record.bed_instance_id != self.instance_id:
            _debug_branch(
                self.args,
                op="reconnect",
                branch="INSTANCE_MISMATCH",
                token=token,
                extra={
                    "claimed_instance_prefix": (store_record.bed_instance_id or "")[:8],
                    "server_instance_prefix": (self.instance_id or "")[:8],
                },
            )
            self.token_store.delete(token)
            return error_envelope(
                CODE_INSTANCE_MISMATCH,
                "Token was issued by a different bed instance",
                recoverable=False,
            )

        live_state = self.sessions.get_by_websocket(new_websocket_id)
        err = self._authorize("reconnect", claims, live_state)
        if err is not None:
            _debug_branch(
                self.args,
                op="reconnect",
                branch="POLICY_DENY",
                token=token,
            )
            return err

        pool = getattr(self.args, "pool", None)
        balance: Optional[int] = None
        try:
            from bbsengine6 import member as _bbs_member
            balance = _bbs_member.getcredits(
                self.args, membermoniker=store_record.moniker, pool=pool
            )
        except Exception:
            balance = None
        info = MemberInfo(
            moniker=store_record.moniker,
            is_sysop=store_record.is_sysop,
            balance=balance,
            loginid=store_record.loginid,
        )
        state = self.sessions.bind(
            store_record.session_id,
            new_websocket_id,
            store_record.moniker,
            store_record.is_sysop,
            loginid=store_record.loginid,
        )
        rotated = self._mint_record(info, store_record.session_id, new_websocket_id)
        try:
            self._persist(rotated)
        except Exception:
            self.token_store.delete(rotated.token)
            raise
        old_token = token
        self.token_store.delete(token)
        state.auth_service_token = rotated.token
        _debug_branch(
            self.args,
            op="reconnect",
            branch="OK",
            token=rotated.token,
            extra={
                "old_tok": _token_hash(old_token),
                "moniker": store_record.moniker,
            },
        )

        pending = self.sessions.take_pending(store_record.session_id)
        io.echo(
            f"AuthService: reconnected moniker={store_record.moniker!r} "
            f"loginid={store_record.loginid!r} "
            f"session={store_record.session_id[:8]}… "
            f"pending={'yes' if pending else 'no'}",
        )
        return self._reconnect_result_envelope(rotated, info, pending)

    async def _handle_auth_refresh(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        token = message.get("token") or ""
        if websocket is None:
            _debug_branch(
                self.args,
                op="refresh",
                branch="NO_WS",
                token=token,
            )
            return error_envelope(
                CODE_NOT_AUTHENTICATED,
                "auth_refresh requires a live websocket",
                recoverable=True,
            )
        websocket_id = str(websocket.id)
        try:
            claims = _decode_token(token, self.secret)
        except TokenError as e:
            _debug_branch(
                self.args,
                op="refresh",
                branch="GARBLED",
                token=token,
                extra={"code": e.code},
            )
            return error_envelope(e.code, str(e), recoverable=False)

        # Expiry is checked BEFORE the store lookup so a token whose
        # clock has run out surfaces as token_expired even when the
        # in-memory store's lazy-GC has already purged the record
        # (which would otherwise mask the expiry as token_revoked).
        # Mirrors casino/api/_auth.py and bed/api/message.py.
        claims_expires_at = float(claims.get("expires_at") or 0.0)
        now_at_expiry = self._now()
        if claims_expires_at <= now_at_expiry:
            _debug_branch(
                self.args,
                op="refresh",
                branch="EXPIRED",
                token=token,
                extra={
                    "expires_at": claims_expires_at,
                    "now": now_at_expiry,
                    "delta": claims_expires_at - now_at_expiry,
                },
            )
            try:
                self.token_store.delete(token)
            except Exception:
                pass
            return error_envelope(
                CODE_TOKEN_EXPIRED,
                "Token has expired; please reconnect",
                recoverable=True,
            )

        store_record = self.token_store.get(token)
        if store_record is None:
            _debug_branch(
                self.args,
                op="refresh",
                branch="REVOKED",
                token=token,
                extra={"store_size": _store_size(self.token_store)},
            )
            return error_envelope(
                CODE_TOKEN_REVOKED,
                "Token is no longer valid",
                recoverable=False,
            )
        if store_record.bed_instance_id != self.instance_id:
            _debug_branch(
                self.args,
                op="refresh",
                branch="INSTANCE_MISMATCH",
                token=token,
            )
            self.token_store.delete(token)
            return error_envelope(
                CODE_INSTANCE_MISMATCH,
                "Token was issued by a different bed instance",
                recoverable=False,
            )

        live_state = self.sessions.get_by_websocket(websocket_id)
        err = self._authorize("refresh", claims, live_state)
        if err is not None:
            _debug_branch(
                self.args,
                op="refresh",
                branch="POLICY_DENY",
                token=token,
            )
            return err

        info = MemberInfo(
            moniker=store_record.moniker,
            is_sysop=store_record.is_sysop,
            balance=store_record.claims.get("balance"),
            loginid=store_record.loginid,
        )
        rotated = self._mint_record(info, store_record.session_id, websocket_id)
        try:
            self._persist(rotated)
        except Exception:
            self.token_store.delete(rotated.token)
            raise
        old_token = token
        self.token_store.delete(token)
        live_state.auth_service_token = rotated.token
        _debug_branch(
            self.args,
            op="refresh",
            branch="OK",
            token=rotated.token,
            extra={"old_tok": _token_hash(old_token)},
        )
        return self._auth_result_envelope(rotated, info, fresh=False)

    async def _handle_auth_revoke(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        token = message.get("token") or ""
        if not token:
            _debug_branch(
                self.args,
                op="revoke",
                branch="EMPTY_TOKEN",
                token=token,
            )
            return error_envelope(
                CODE_TOKEN_INVALID, "token required", recoverable=False
            )
        try:
            claims = _decode_token(token, self.secret)
        except TokenError as e:
            _debug_branch(
                self.args,
                op="revoke",
                branch="GARBLED",
                token=token,
                extra={"code": e.code},
            )
            return {
                "type": "auth_revoke_result",
                "success": False,
                "code": e.code,
                "recoverable": False,
            }
        err = self._authorize("revoke", claims, None)
        if err is not None:
            _debug_branch(
                self.args,
                op="revoke",
                branch="POLICY_DENY",
                token=token,
            )
            return {
                "type": "auth_revoke_result",
                "success": False,
                "code": CODE_FORBIDDEN,
                "recoverable": False,
            }
        deleted = self.token_store.delete(token)
        _debug_branch(
            self.args,
            op="revoke",
            branch="OK" if deleted else "ALREADY_GONE",
            token=token,
            extra={"store_size": _store_size(self.token_store)},
        )
        return {
            "type": "auth_revoke_result",
            "success": bool(deleted),
            "code": None if deleted else CODE_TOKEN_REVOKED,
            "recoverable": not deleted,
        }

    def _auth_result_envelope(
        self, record: TokenRecord, info: MemberInfo, *, fresh: bool
    ) -> Dict[str, Any]:
        env: Dict[str, Any] = {
            "type": "auth_result",
            "success": True,
            "moniker": info.moniker,
            "is_sysop": bool(info.is_sysop),
            "session_id": record.session_id,
            "token": record.token,
            "expires_at": _iso_from_ts(record.expires_at),
        }
        if info.balance is not None:
            env["balance"] = int(info.balance)
        else:
            env["balance"] = 0
        if fresh:
            env["message"] = "Authenticated"
        return env

    def _reconnect_result_envelope(
        self,
        record: TokenRecord,
        info: MemberInfo,
        pending: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        env: Dict[str, Any] = {
            "type": "reconnect_result",
            "success": True,
            "moniker": info.moniker,
            "is_sysop": bool(info.is_sysop),
            "session_id": record.session_id,
            "token": record.token,
            "expires_at": _iso_from_ts(record.expires_at),
            "balance": int(info.balance) if info.balance is not None else 0,
            "replayed": pending,
        }
        if pending is not None:
            env["replayed_request_id"] = pending.get("request_id")
        return env


# Op -> handler dispatch. Keeps handle_message() a flat dict lookup and
# makes it obvious at import time that every op has exactly one handler.
_OP_TO_HANDLER = {
    "login": AuthService._handle_auth,
    "reconnect": AuthService._handle_reconnect,
    "refresh": AuthService._handle_auth_refresh,
    "revoke": AuthService._handle_auth_revoke,
}
