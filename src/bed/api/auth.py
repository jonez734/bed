# bed/api/auth.py
# AuthService: short-lived signed bearer tokens for bed.

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bbsengine6 import io

from .credential_provider import CredentialProvider
from .errors import (
    CODE_BAD_CREDENTIALS,
    CODE_INSTANCE_MISMATCH,
    CODE_MISSING_CREDENTIALS,
    CODE_NOT_AUTHENTICATED,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    CODE_TOKEN_REVOKED,
    error_envelope,
    scrub_token,
)
from .handler import BaseService
from .session import SessionRegistry
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
        if msg_type == "auth":
            return await self._handle_auth(websocket, message)
        if msg_type == "reconnect":
            return await self._handle_reconnect(websocket, message)
        if msg_type == "auth_refresh":
            return await self._handle_auth_refresh(websocket, message)
        if msg_type == "auth_revoke":
            return await self._handle_auth_revoke(websocket, message)
        return None

    async def _handle_auth(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        moniker = (message.get("moniker") or "").strip()
        password = message.get("password") or ""
        if not moniker or not password:
            return error_envelope(
                CODE_MISSING_CREDENTIALS,
                "moniker and password are required",
                recoverable=False,
            )

        info = self.credential_provider.authenticate(
            self.args, moniker, password, pool=getattr(self.args, "pool", None)
        )
        if info is None:
            return error_envelope(
                CODE_BAD_CREDENTIALS,
                "Invalid moniker or password",
                recoverable=False,
            )

        session_id = str(uuid.uuid4())
        websocket_id = str(websocket.id)
        state = self.sessions.bind(
            session_id, websocket_id, info.moniker, info.is_sysop, balance=info.balance
        )
        state.auth_service_token = None
        record = self._mint_record(info, session_id, websocket_id)
        self._persist(record)
        state.auth_service_token = record.token

        io.echo(
            f"AuthService: issued token for moniker={info.moniker!r} "
            f"session={session_id[:8]}…",
        )
        return self._auth_result_envelope(record, info, fresh=True)

    async def _handle_reconnect(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        token = message.get("token") or ""
        new_websocket_id = str(websocket.id)

        try:
            claims = _decode_token(token, self.secret)
        except TokenError as e:
            return error_envelope(e.code, str(e), recoverable=False)

        store_record = self.token_store.get(token)
        if store_record is None:
            existing = self.sessions.get_by_session(claims.get("session_id", ""))
            if existing is not None:
                existing.pending_request = None
            return error_envelope(
                CODE_TOKEN_REVOKED,
                "Token is no longer valid",
                recoverable=False,
            )

        if store_record.bed_instance_id != self.instance_id:
            self.token_store.delete(token)
            return error_envelope(
                CODE_INSTANCE_MISMATCH,
                "Token was issued by a different bed instance",
                recoverable=False,
            )
        if store_record.expires_at <= self._now():
            self.token_store.delete(token)
            return error_envelope(
                CODE_TOKEN_EXPIRED,
                "Token has expired",
                recoverable=True,
            )

        info = MemberInfo(
            moniker=store_record.moniker,
            is_sysop=store_record.is_sysop,
            balance=None,
        )
        state = self.sessions.bind(
            store_record.session_id,
            new_websocket_id,
            store_record.moniker,
            store_record.is_sysop,
        )
        rotated = self._mint_record(info, store_record.session_id, new_websocket_id)
        try:
            self._persist(rotated)
        except Exception:
            self.token_store.delete(rotated.token)
            raise
        self.token_store.delete(token)
        state.auth_service_token = rotated.token

        pending = self.sessions.take_pending(store_record.session_id)
        io.echo(
            f"AuthService: reconnected moniker={store_record.moniker!r} "
            f"session={store_record.session_id[:8]}… "
            f"pending={'yes' if pending else 'no'}",
        )
        return self._reconnect_result_envelope(rotated, info, pending)

    async def _handle_auth_refresh(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        token = message.get("token") or ""
        if websocket is None:
            return error_envelope(
                CODE_NOT_AUTHENTICATED,
                "auth_refresh requires a live websocket",
                recoverable=True,
            )
        websocket_id = str(websocket.id)
        try:
            _claims = _decode_token(token, self.secret)
        except TokenError as e:
            return error_envelope(e.code, str(e), recoverable=False)

        store_record = self.token_store.get(token)
        if store_record is None:
            return error_envelope(
                CODE_TOKEN_REVOKED,
                "Token is no longer valid",
                recoverable=False,
            )
        if store_record.bed_instance_id != self.instance_id:
            self.token_store.delete(token)
            return error_envelope(
                CODE_INSTANCE_MISMATCH,
                "Token was issued by a different bed instance",
                recoverable=False,
            )
        if store_record.expires_at <= self._now():
            self.token_store.delete(token)
            return error_envelope(
                CODE_TOKEN_EXPIRED,
                "Token has expired; please reconnect",
                recoverable=True,
            )

        live_state = self.sessions.get_by_websocket(websocket_id)
        if live_state is None or live_state.session_id != store_record.session_id:
            return error_envelope(
                CODE_NOT_AUTHENTICATED,
                "auth_refresh requires the original socket; use reconnect",
                recoverable=True,
            )

        info = MemberInfo(
            moniker=store_record.moniker,
            is_sysop=store_record.is_sysop,
            balance=store_record.claims.get("balance"),
        )
        rotated = self._mint_record(info, store_record.session_id, websocket_id)
        try:
            self._persist(rotated)
        except Exception:
            self.token_store.delete(rotated.token)
            raise
        self.token_store.delete(token)
        live_state.auth_service_token = rotated.token
        return self._auth_result_envelope(rotated, info, fresh=False)

    async def _handle_auth_revoke(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        token = message.get("token") or ""
        if not token:
            return error_envelope(
                CODE_TOKEN_INVALID, "token required", recoverable=False
            )
        try:
            _claims = _decode_token(token, self.secret)
        except TokenError as e:
            return {
                "type": "auth_revoke_result",
                "success": False,
                "code": e.code,
                "recoverable": False,
            }
        deleted = self.token_store.delete(token)
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
            "replayed": pending,
        }
        if pending is not None:
            env["replayed_request_id"] = pending.get("request_id")
        return env
