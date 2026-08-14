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
# TODO: define ``bbsengine6.message.access(args, op, **kwargs)`` and
# route ``message_subscribe`` / ``message_unsubscribe`` /
# ``message_list_pending`` through it, following the
# bbsengine6.bank.access() pattern in bed/api/bank.py. Suggested op
# vocabulary: "subscribe", "unsubscribe", "list_pending".
#
# NOTE: today anyone can subscribe to anyone's notifications or read
# anyone's pending message queue. This is a real authorization gap;
# once access() lands it should enforce a self-moniker-or-sysop
# rule (mirrors bbsengine6.bank.access("balance")), and the bed
# handlers should delegate to it instead of accepting any moniker
# from the wire.

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

try:
    import psycopg
except ImportError:
    psycopg = None  # type: ignore[assignment]

from bbsengine6 import io
from bbsengine6.database import make_dsn
from bbsengine6.message import get_pending_messages

from .handler import BaseService

logger = logging.getLogger(__name__)


NOTIFY_CHANNEL = "engine_message_recipient"


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
        if msg_type == "message_subscribe":
            return await self._handle_subscribe(websocket, message)
        if msg_type == "message_unsubscribe":
            return await self._handle_unsubscribe(websocket, message)
        if msg_type == "message_list_pending":
            return await self._handle_list_pending(message)
        return None

    async def _handle_subscribe(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        moniker = (message.get("moniker") or "").strip()
        if not moniker:
            return {
                "type": "message_subscribe_result",
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
            }
        # TODO: gate on
        # ``bbsengine6.message.access(args, "subscribe", session=state,
        # message=msg)`` once that lands; require self-moniker-or-sysop.
        async with self._subscribed_lock:
            self._subscribed[moniker] = websocket
        logger.info("MessageService: subscribed moniker=%s", moniker)
        return {"type": "message_subscribe_result", "ok": True, "moniker": moniker}

    async def _handle_unsubscribe(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        moniker = (message.get("moniker") or "").strip()
        if not moniker:
            return {
                "type": "message_unsubscribe_result",
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
            }
        async with self._subscribed_lock:
            self._subscribed.pop(moniker, None)
        return {"type": "message_unsubscribe_result", "ok": True, "moniker": moniker}

    async def _handle_list_pending(
        self, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        # TODO: gate on
        # ``bbsengine6.message.access(args, "list_pending", session=state,
        # message=msg)`` once that lands; require self-moniker-or-sysop.
        moniker = (message.get("moniker") or "").strip()
        if not moniker:
            return {
                "type": "message_list_pending_result",
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
                "messages": [],
            }
        try:
            messages = get_pending_messages(moniker, limit=100)
        except Exception as e:
            io.echo_traceback("bed.api.message._handle_list_pending:")
            return {
                "type": "message_list_pending_result",
                "ok": False,
                "code": "db_error",
                "message": str(e),
                "messages": [],
            }
        return {
            "type": "message_list_pending_result",
            "ok": True,
            "moniker": moniker,
            "messages": messages,
        }
