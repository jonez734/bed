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
#   Handlers perform two gates in order:
#     1. Session bound (else ``not_authenticated``).
#     2. Wire-shape validation -- moniker present (else
#        ``missing_moniker``). Stays in the handler because envelope
#        codes are a wire-protocol concern.
#     3. ``bbsengine6.message.access()`` authorization (else
#        ``forbidden``). The access rule is "self-moniker-or-sysop",
#        which closes the prior authorization gap where anyone could
#        subscribe to anyone's NOTIFY stream or read anyone's pending
#        queue.
#   The bbsengine6.message.access function never reads the raw token
#   or the websocket id; it only sees the wire-shaped payload.

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

from .errors import (
    CODE_DATABASE_ERROR,
    error_envelope,
    forbidden,
    not_authenticated,
)
from .handler import BaseService
from .session import SessionState

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


def _get_session_for(
    self_ref: "MessageService", websocket: Any
) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
    """Look up the SessionState bound to ``websocket``.

    Returns ``(state, None)`` on success or ``(None, error_envelope)``
    when no session is bound (the websocket has not completed
    ``auth``/``reconnect``/``auth_refresh``). Mirrors the equivalent
    helper in bed/api/bank.py.
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
    """

    HANDLED_TYPES = (
        "message_subscribe",
        "message_unsubscribe",
        "message_list_pending",
    )

    def __init__(self, args: Any, session_manager: Any) -> None:
        super().__init__(args, session_manager)
        self.server: Any = None
        self._subscribed: Dict[str, Any] = {}
        self._subscribed_lock = asyncio.Lock()
        self._listener_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._async_conn: Any = None
        self._seq = 0

    def register_all(self, server: Any) -> None:
        self.server = server
        server.register_service(self, list(self.HANDLED_TYPES))

    def _check_access(
        self, websocket: Any, op: str, message: Dict[str, Any]
    ) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
        """Run the three access gates in order: session, shape, authz.

        Returns ``(state, None)`` on success or ``(state_or_None,
        error_envelope)`` on failure. The caller uses the returned
        envelope as the wire response and stops processing. Mirrors
        ``bed.api.bank.BankService._check_access``.
        """
        state, err = _get_session_for(self, websocket)
        if err is not None:
            return None, err

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
            state.moniker,
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
# handler. Mirrors bed/api/bank.py:534-544.
_OP_TO_HANDLER = {
    "subscribe": MessageService._handle_subscribe,
    "unsubscribe": MessageService._handle_unsubscribe,
    "list_pending": MessageService._handle_list_pending,
}
