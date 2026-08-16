# bed/api/message.py
# MessageService: server-push notifications via PostgreSQL LISTEN/NOTIFY.
#
# Subscribes to the `engine_message_recipient` channel (populated by
# AFTER INSERT/UPDATE triggers on engine.__message_recipient) and fans
# out to connected WebSocket clients by recipient_moniker.
#
# Wire protocol:
#   Request:  {"type": "message_subscribe",    "moniker": "<user>"}
#             {"type": "message_unsubscribe",  "moniker": "<user>"}
#             {"type": "message_list_pending", "moniker": "<user>"}
#   Response: {"type": "message_subscribe_result",    "moniker": ..., "ok": true}
#             {"type": "message_unsubscribe_result",  "moniker": ..., "ok": true}
#             {"type": "message_list_pending_result", "moniker": ..., "messages": [...]}
#   Push:     {"type": "message", "channel": "engine_message_recipient",
#              "message_id": N, "recipient_id": N, "status": ...,
#              "urgency": "...", "datestamp": "..."}
#
# Authentication / authorization:
#   Every handler delegates its per-op policy decision to
#   ``bbsengine6.message.access(args, op, session=live_state,
#   message=msg)``. The bbsengine6.message package owns the op
#   vocabulary ("subscribe", "unsubscribe", "list_pending") and the
#   per-op policy; this module is the bed-side consumer, parallel to
#   bed/api/bank.py for bank and bed/api/auth.py for auth.
#
#   Handlers perform FIVE gates in order, mirroring the bank/auth
#   standard:
#     1. Session resolve via :func:`_get_or_bind_session_for` -- looks
#        up the WS-bound :class:`SessionState`, or lazily binds one
#        from a valid wire token's claims when no session is bound yet
#        (the CLI's per-subcommand ``asyncio.run`` cycle closes its
#        event loop and forces a fresh WebSocket on the next call).
#        Returns ``not_authenticated`` only when both lookups miss.
#     2. Wire-token validation -- when ``message["token"]`` is present,
#        decode + HMAC verify + store check + expiry + instance match
#        (else ``token_invalid`` / ``token_revoked`` /
#        ``bed_instance_mismatch`` / ``token_expired``). The wire
#        token is preferred over the session-bound snapshot because it
#        is read from the token file on the client just before the WS
#        send and catches the case where the session-bound snapshot has
#        been revoked since WS open. Decoded claims are stashed on
#        ``message["claims"]`` so :func:`bbsengine6.message.access`
#        can prefer claim-derived ``moniker`` / ``is_sysop`` over the
#        in-memory session.
#     3. Session-token validation -- only when the wire token is
#        absent. Reads ``state.auth_service_token`` (set by the auth
#        flow at WS bind time, or by the lazy-bind fallback above).
#     4. Wire-shape validation -- moniker present (else
#        ``missing_moniker``). Stays in the handler because envelope
#        codes are a wire-protocol concern.
#     5. ``bbsengine6.message.access()`` authorization (else
#        ``forbidden``). The access rule is "self-moniker-or-sysop",
#        which closes the prior authorization gap where anyone could
#        subscribe to anyone's NOTIFY stream or read anyone's pending
#        queue.
#
#   When the service is constructed without ``secret`` /
#   ``token_store`` / ``instance_id`` the token gates become no-ops
#   (legacy callers and ``--token-persistence=none`` mode); the
#   per-op policy still runs and authorization falls back to the
#   session attributes directly.
#
#   :func:`bbsengine6.message.access` never reads the raw token or the
#   websocket id; it only sees the wire-shaped payload (and the
#   ``message["claims"]`` sub-dict the handler stashed there).

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Tuple

try:
    import psycopg
except ImportError:
    psycopg = None  # type: ignore[assignment]

from bbsengine6 import io
from bbsengine6.database import make_dsn
from bbsengine6.message import access as _message_access
from bbsengine6.message import get_pending_messages

from .auth import TokenError, _decode_token
from .errors import (
    CODE_DATABASE_ERROR,
    CODE_INSTANCE_MISMATCH,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_REVOKED,
    error_envelope,
    forbidden,
    not_authenticated,
)
from .handler import BaseService
from .session import SessionState
from .token_store import TokenStore

logger = logging.getLogger(__name__)


NOTIFY_CHANNEL = "engine_message_recipient"


CODE_MISSING_MONIKER = "missing_moniker"


# Map from WS ``type`` field to the domain verb understood by
# ``bbsengine6.message.access``. The message module owns the verb
# vocabulary; this dict is the only place bed-side code needs to
# maintain the translation.
_TYPE_TO_OP: Dict[str, str] = {
    "message_subscribe": "subscribe",
    "message_unsubscribe": "unsubscribe",
    "message_list_pending": "list_pending",
}


def _validate_token_against_store(
    self_ref: "MessageService", token: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Decode ``token`` and check it against the token store + instance.

    Shared core for :func:`_validate_session_token` and
    :func:`_validate_wire_token`. Returns the same ``(claims, None)``
    / ``(None, error_envelope)`` tuple shape.

    A ``token`` of ``""`` / ``None`` returns ``(None, None)`` (no
    token was supplied). An unset ``secret`` / ``token_store`` /
    ``instance_id`` also returns ``(None, None)`` so legacy /
    unit-test callers that constructed the service without token
    wiring degrade gracefully to session-bound authorization.

    Expiry is checked BEFORE the store lookup so a token whose clock
    has run out surfaces as ``token_expired`` even when the
    in-memory store's lazy-GC has already purged the record (which
    would otherwise mask the expiry as ``token_revoked``).
    """
    token = (token or "").strip()
    if not token:
        return None, None

    secret = getattr(self_ref, "secret", None)
    token_store = getattr(self_ref, "token_store", None)
    instance_id = getattr(self_ref, "instance_id", None)
    if not secret or token_store is None or not instance_id:
        return None, None

    try:
        claims = _decode_token(token, secret)
    except TokenError as e:
        return None, error_envelope(e.code, str(e), recoverable=False)

    now_fn = getattr(self_ref, "_now", None)
    now = float(now_fn()) if callable(now_fn) else None
    expires_at_claim = float(claims.get("expires_at") or 0.0)
    if now is not None and expires_at_claim <= now:
        try:
            token_store.delete(token)
        except Exception:
            pass
        return None, error_envelope(
            CODE_TOKEN_EXPIRED,
            "Token has expired",
            recoverable=True,
        )

    store_record = token_store.get(token)
    if store_record is None:
        return None, error_envelope(
            CODE_TOKEN_REVOKED,
            "Token is no longer valid",
            recoverable=False,
        )
    if store_record.bed_instance_id != instance_id:
        token_store.delete(token)
        return None, error_envelope(
            CODE_INSTANCE_MISMATCH,
            "Token was issued by a different bed instance",
            recoverable=False,
        )

    return claims, None


def _validate_session_token(
    self_ref: "MessageService", state: SessionState
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Validate ``state.auth_service_token`` if it is set.

    Returns ``(claims, None)`` on success -- ``claims`` is the decoded
    token claims dict, ready to be stashed on ``message["claims"]`` so
    :func:`bbsengine6.message.access` can prefer claim-derived
    ``moniker`` / ``is_sysop`` over the in-memory session state.

    Returns ``(None, None)`` when the session has no token (e.g. it
    was created outside the auth flow); the caller falls back to the
    existing session-based authorization.

    Returns ``(None, error_envelope)`` on any token failure: the
    caller surfaces the envelope and stops processing. Envelope codes
    mirror the auth service so clients can reuse their reconnect
    logic: ``token_invalid`` for HMAC / shape failures,
    ``token_revoked`` for store misses, ``bed_instance_mismatch``
    for tokens minted by a different bed instance, ``token_expired``
    for stale tokens.
    """
    return _validate_token_against_store(
        self_ref, getattr(state, "auth_service_token", None)
    )


def _validate_wire_token(
    self_ref: "MessageService", message: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Validate ``message["token"]`` if it is present.

    Companion to :func:`_validate_session_token`. The CLI sends the
    bearer token it read from its ``--token-file`` on every wire
    call; the server validates it independently of (and in
    preference to) the WS-bound session token. This is the
    defense-in-depth path: a token revoked since the WS opened
    cannot drive a NOTIFY subscription even if the session-bound
    snapshot is stale.

    Returns ``(None, None)`` when ``message["token"]`` is empty or
    absent so legacy callers (and tests that don't care about the
    wire token) fall through to the session-bound path.
    """
    return _validate_token_against_store(
        self_ref, message.get("token") or ""
    )


def _get_session_for(
    self_ref: "MessageService", websocket: Any
) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
    """Look up the SessionState bound to ``websocket``.

    Returns ``(state, None)`` on success or ``(None, error_envelope)``
    when no session is bound (the websocket has not completed
    ``auth``/``reconnect``/``auth_refresh``). The lazy-bind fallback
    from a wire token lives in :func:`_get_or_bind_session_for`,
    which is what :meth:`MessageService._check_access` calls.
    """
    if websocket is None:
        return None, not_authenticated()
    try:
        ws_id = str(websocket.id)
    except Exception:
        return None, not_authenticated()
    state = self_ref.sessions.get_by_websocket(ws_id)
    if state is None:
        return None, not_authenticated()
    return state, None


def _get_or_bind_session_for(
    self_ref: "MessageService",
    websocket: Any,
    message: Dict[str, Any],
) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
    """Return the session for ``websocket``, lazily binding from a
    valid wire token when no session is bound yet.

    The CLI's ``message`` tool runs each per-op call under a fresh
    ``asyncio.run`` (one per subcommand) which closes its event
    loop and forces :class:`BedConnection` to open a new WebSocket
    on the next call. Each new WebSocket is a fresh ``websocket.id``
    in the server's eyes, so without this fallback the session
    registered by the prior ``auth reconnect`` is no longer
    reachable and every message op returns ``not_authenticated``.

    The fallback mirrors the ``auth reconnect`` handshake: when a
    valid wire token is present, its ``session_id`` / ``moniker`` /
    ``is_sysop`` / ``loginid`` claims are used to either re-bind an
    existing :class:`SessionState` (its WS mapping is updated) or
    synthesize a fresh one if the server's session registry has
    lost the entry (e.g. after a process restart). The wire token
    becomes the new ``state.auth_service_token`` so subsequent
    defense-in-depth checks see a consistent snapshot. The
    validated claims are stashed on ``message["claims"]`` so the
    downstream :func:`bbsengine6.message.access` call can prefer
    claim-derived ``moniker`` / ``is_sysop`` over the in-memory
    session attributes.

    Returns the same ``(state, err)`` tuple shape as
    :func:`_get_session_for`. ``err`` is set on:

    * No bound session AND no wire token: ``not_authenticated``
      (a caller that never sent ``auth`` / ``reconnect`` AND
      never sent a token -- the legacy unauthenticated case).
    * Bound session absent, wire token present but invalid:
      the envelope from :func:`_validate_wire_token`
      (``token_invalid`` / ``token_revoked`` /
      ``bed_instance_mismatch`` / ``token_expired``).
    """
    state, err = _get_session_for(self_ref, websocket)
    if state is not None:
        return state, None

    wire_token = (message.get("token") or "").strip()
    if not wire_token:
        return None, not_authenticated()

    claims, token_err = _validate_wire_token(self_ref, message)
    if token_err is not None:
        return None, token_err
    if claims is None:
        return None, not_authenticated()

    session_id = (claims.get("session_id") or "").strip()
    if not session_id:
        return None, not_authenticated()

    try:
        ws_id = str(websocket.id)
    except Exception:
        return None, not_authenticated()

    existing = self_ref.sessions.get_by_session(session_id)
    moniker = (claims.get("moniker") or "").strip()
    is_sysop = bool(claims.get("is_sysop", False))
    loginid = claims.get("loginid")

    if existing is not None:
        state = self_ref.sessions.bind(
            session_id,
            ws_id,
            existing.moniker or moniker,
            bool(existing.is_sysop) or is_sysop,
            loginid=existing.loginid or loginid,
        )
    else:
        state = self_ref.sessions.bind(
            session_id,
            ws_id,
            moniker,
            is_sysop,
            loginid=loginid,
        )

    state.auth_service_token = wire_token
    message["claims"] = claims
    return state, None


def _validate_shape(
    op: str, message: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Validate the wire-shape invariants ``bbsengine6.message.access``
    intentionally does not check.

    All three ops require a non-empty ``moniker``. Returns ``None`` on
    success or an error envelope on failure.
    """
    moniker = (message.get("moniker") or "").strip()
    if not moniker:
        return error_envelope(
            CODE_MISSING_MONIKER, "moniker is required"
        )
    return None


class MessageService(BaseService):
    """Server-push message notifications.

    Subscribes to PostgreSQL NOTIFY on `engine_message_recipient` and
    pushes each notification to the connected WebSocket client whose
    moniker matches the recipient_moniker in the payload.

    The service maintains a per-moniker subscription map. Clients
    register via `message_subscribe` and can query their pending
    messages on reconnect via `message_list_pending`.

    Token-aware wiring: when the constructor receives ``secret``,
    ``token_store``, and ``instance_id`` (the same values the auth
    service uses), every ``_handle_*`` re-verifies the session's
    bearer token against the HMAC scheme and the token store. The
    decoded claims are stashed on ``message["claims"]`` so the
    downstream policy call prefers claim-derived ``moniker`` /
    ``is_sysop`` over the in-memory session attributes (defense in
    depth against stale or compromised sessions).
    """

    HANDLED_TYPES = (
        "message_subscribe",
        "message_unsubscribe",
        "message_list_pending",
    )

    def __init__(
        self,
        args: Any,
        session_manager: Any,
        *,
        secret: Optional[bytes] = None,
        token_store: Optional[TokenStore] = None,
        instance_id: Optional[str] = None,
        clock: Optional[Any] = None,
    ) -> None:
        """Construct a MessageService.

        ``secret``, ``token_store``, and ``instance_id`` are the
        token-aware wiring that lets :meth:`_check_access` re-verify
        ``state.auth_service_token`` on every message op. They are
        all optional; if any is missing, the service falls back to
        session-based authorization without token re-verification
        (legacy / ``--token-persistence=none`` mode). ``clock`` is
        the injectable time source used by AuthService for
        deterministic expiry tests.
        """
        super().__init__(args, session_manager)
        self.server: Any = None
        self._subscribed: Dict[str, Any] = {}
        self._subscribed_lock = asyncio.Lock()
        self._listener_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._async_conn: Any = None
        self._seq = 0
        self.secret = bytes(secret) if secret else None
        self.token_store = token_store
        self.instance_id = str(instance_id) if instance_id else None
        self._clock = clock

    def register_all(self, server: Any) -> None:
        self.server = server
        server.register_service(self, list(self.HANDLED_TYPES))

    def _now(self) -> float:
        """Return the current UNIX timestamp, honoring ``clock`` if set."""
        if self._clock is not None:
            return float(self._clock())
        import time as _time

        return _time.time()

    def _check_access(
        self, websocket: Any, op: str, message: Dict[str, Any]
    ) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
        """Run the five access gates in order: session, wire-token, session-token, shape, authz.

        Returns ``(state, None)`` on success or ``(state_or_None,
        error_envelope)`` on failure. The caller uses the returned
        envelope as the wire response and stops processing. Mirrors
        ``bed.api.bank.BankService._check_access`` so a token minted
        by ``bed.api.auth.AuthService`` is consumable here without
        any re-implementation.

        Session resolution: :func:`_get_or_bind_session_for` looks
        up the WS-bound session first. When none is bound (the CLI
        just opened a fresh WebSocket after an ``asyncio.run`` cycle
        closed the previous loop) it falls back to validating the
        wire token and lazily binding the session from the token's
        claims -- mirroring the ``auth reconnect`` handshake. This
        keeps the per-call token path (``defense-in-depth``) and the
        WS-bound session in sync without requiring the CLI to drive
        a single persistent event loop.

        Two token gates, ordered by preference:

        1. ``_validate_wire_token`` reads ``message["token"]`` (the
           bearer token the CLI sent on this call). When non-empty,
           its claims are stashed on ``message["claims"]`` and the
           session-bound gate is skipped -- the wire token is more
           recently captured (read from the token file on the client
           just before the WS send) and catches the case where the
           session-bound snapshot is stale (e.g. revoked since
           ``auth reconnect``). This is the defense-in-depth path.
        2. ``_validate_session_token`` reads
           ``state.auth_service_token`` (set by the auth flow at WS
           bind time, or by the lazy-bind fallback above). Used
           only when the wire token is absent -- legacy callers
           and tests that don't supply a per-call token still get
           session-bound authorization.

        Both gates share
        :func:`_validate_token_against_store`. When the service was
        constructed without token-aware wiring (legacy / unit-test
        usage), both gates are no-ops and authorization falls back
        to the session attributes directly.
        """
        state, err = _get_or_bind_session_for(self, websocket, message)
        if err is not None:
            return None, err

        if "claims" not in message:
            claims, err = _validate_wire_token(self, message)
            if err is not None:
                return state, err
            if claims is not None:
                message["claims"] = claims
            else:
                claims, err = _validate_session_token(self, state)
                if err is not None:
                    return state, err
                if claims is not None:
                    message["claims"] = claims

        err = _validate_shape(op, message)
        if err is not None:
            return state, err

        if not _message_access(
            self.args, op, session=state, message=message
        ):
            return state, forbidden(
                "Operation not permitted for this account"
            )

        return state, None

    async def start_listener(self) -> None:
        """Start the LISTEN background task.

        Idempotent: a second call while already running is a no-op.
        """
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._stop_event.clear()
        self._listener_task = asyncio.create_task(
            self._listen_loop(), name="bed-message-listener"
        )
        logger.info("MessageService: listener started")

    async def stop_listener(self) -> None:
        """Stop the LISTEN background task and release the async connection."""
        self._stop_event.set()
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None
        await self._close_async_conn()
        logger.info("MessageService: listener stopped")

    async def _close_async_conn(self) -> None:
        """Close the async connection if one is open. Idempotent."""
        conn = self._async_conn
        if conn is None:
            return
        self._async_conn = None
        try:
            await conn.close()
        except Exception as e:
            logger.warning("MessageService: error closing async conn: %s", e)

    async def _listen_loop(self) -> None:
        """Long-lived LISTEN loop on the engine_message_recipient channel.

        Holds a dedicated psycopg AsyncConnection (bypassing the
        ConnectionPool) because LISTEN registrations are per-connection.
        """
        if psycopg is None:
            logger.error("MessageService: psycopg not installed; listener disabled")
            return

        dsn = make_dsn(self.args)
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                self._async_conn = await psycopg.AsyncConnection.connect(
                    dsn, autocommit=True
                )
                async with self._async_conn.cursor() as cur:
                    await cur.execute(f"LISTEN {NOTIFY_CHANNEL}")
                logger.info(
                    "MessageService: LISTEN %s established", NOTIFY_CHANNEL
                )
                backoff = 1.0

                while not self._stop_event.is_set():
                    notifies = await self._async_conn.notifies(timeout=1.0)
                    for n in notifies:
                        await self._dispatch_notification(n.payload)
            except asyncio.CancelledError:
                raise
            except (psycopg.Error, OSError) as e:
                logger.warning(
                    "MessageService: listener error (will retry in %.1fs): %s",
                    backoff,
                    e,
                )
                await self._close_async_conn()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=backoff
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
            finally:
                await self._close_async_conn()

    async def _dispatch_notification(self, payload: str) -> None:
        """Parse a NOTIFY payload and push to the matching websocket."""
        try:
            data = json.loads(payload)
        except (TypeError, ValueError) as e:
            logger.warning("MessageService: bad payload %r: %s", payload, e)
            return
        if not isinstance(data, dict):
            logger.warning(
                "MessageService: payload not a JSON object: %r", payload
            )
            return

        recipient = data.get("recipient_moniker")
        if not recipient:
            return
        async with self._subscribed_lock:
            ws = self._subscribed.get(recipient)
        if ws is None or self.server is None:
            return

        self._seq += 1
        envelope = {
            "type": "message",
            "channel": NOTIFY_CHANNEL,
            "message_id": data.get("message_id"),
            "recipient_id": data.get("recipient_id"),
            "recipient_moniker": recipient,
            "status": data.get("status"),
            "urgency": data.get("urgency"),
            "datestamp": data.get("datestamp"),
            "request_id": f"server:msg:{self._seq}",
        }
        try:
            await self.server.send_to(ws, envelope)
        except Exception as e:
            logger.warning("MessageService: send_to failed for %s: %s", recipient, e)
            async with self._subscribed_lock:
                self._subscribed.pop(recipient, None)

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        op = _TYPE_TO_OP.get(msg_type)
        if op is None:
            return None
        handler = _OP_TO_HANDLER[op]
        return await handler(self, websocket, message)

    async def _handle_subscribe(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._check_access(websocket, "subscribe", message)
        if err is not None:
            return {
                "type": "message_subscribe_result",
                "ok": False,
                "code": err.get("code"),
                "message": err.get("message"),
                "recoverable": err.get("recoverable", False),
            }
        moniker = (message.get("moniker") or "").strip()
        async with self._subscribed_lock:
            self._subscribed[moniker] = websocket
        logger.info(
            "MessageService: subscribed moniker=%s by=%s",
            moniker,
            getattr(state, "moniker", "<unknown>"),
        )
        return {
            "type": "message_subscribe_result",
            "ok": True,
            "moniker": moniker,
        }

    async def _handle_unsubscribe(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._check_access(websocket, "unsubscribe", message)
        if err is not None:
            return {
                "type": "message_unsubscribe_result",
                "ok": False,
                "code": err.get("code"),
                "message": err.get("message"),
                "recoverable": err.get("recoverable", False),
            }
        moniker = (message.get("moniker") or "").strip()
        async with self._subscribed_lock:
            self._subscribed.pop(moniker, None)
        return {
            "type": "message_unsubscribe_result",
            "ok": True,
            "moniker": moniker,
        }

    async def _handle_list_pending(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._check_access(websocket, "list_pending", message)
        if err is not None:
            return {
                "type": "message_list_pending_result",
                "ok": False,
                "messages": [],
                "code": err.get("code"),
                "message": err.get("message"),
                "recoverable": err.get("recoverable", False),
            }
        moniker = (message.get("moniker") or "").strip()
        try:
            messages = get_pending_messages(moniker, limit=100)
        except Exception as e:
            io.echo_traceback("bed.api.message._handle_list_pending:")
            return {
                "type": "message_list_pending_result",
                "ok": False,
                "code": CODE_DATABASE_ERROR,
                "message": str(e),
                "messages": [],
            }
        return {
            "type": "message_list_pending_result",
            "ok": True,
            "moniker": moniker,
            "messages": messages,
        }


# Op -> handler dispatch. Keeps handle_message() a flat dict lookup and
# makes it obvious at import time that every op has exactly one
# handler. Mirrors bed/api/bank.py:831-841.
_OP_TO_HANDLER = {
    "subscribe": MessageService._handle_subscribe,
    "unsubscribe": MessageService._handle_unsubscribe,
    "list_pending": MessageService._handle_list_pending,
}
