"""Client for the bed MessageService.

Wraps a :class:`BedConnection` with a small convenience API:
``subscribe(moniker)`` registers a server-side subscription and starts
a background recv task that updates the local ``bbsengine6.message``
unread count cache. ``list_pending(moniker)`` returns the messages
the server has buffered for the user. ``unsubscribe(moniker)`` tears
down both the server-side and client-side state.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bed.client.connection import BedConnection, PushHandler
from bed.client.exceptions import BedUnavailable

logger = logging.getLogger(__name__)


def _exact_unread(moniker: str) -> int:
    """Authoritative unread count from the DB (pending + delivered).

    Runs on the push executor thread. Returns 0 if bbsengine6 cannot
    count (no DB configured), so a failed recount never raises into the
    push path.
    """
    from bbsengine6 import message as message_module

    try:
        return int(message_module.get_unread_count(moniker) or 0)
    except Exception:
        return 0


class BedMessageServiceClient:
    """Client for bed's MessageService.

    The client owns one :class:`BedConnection` (lazily opened on the
    first operation) and one push handler. It uses the
    ``bbsengine6.message`` module's local cache to track unread
    counts so the bbsengine6 TUI can read counts without a DB hit.
    """

    def __init__(
        self,
        connection: BedConnection,
        *,
        token: str = "",
        token_file: Optional[str] = None,
        owner: bool = False,
    ) -> None:
        """
        Args:
            connection: the shared BedConnection (never read directly here).
            token: bearer token used on every message op.
            token_file: path the token was read from. Used to re-read on
                ``not_authenticated`` (non-owner) and to write rotations (owner).
            owner: True only for the process that owns the connection. Only
                the owner writes rotated tokens back to ``token_file``.
        """
        self._conn = connection
        self._subscribed_moniker: Optional[str] = None
        self._handler: Optional[PushHandler] = None
        self._token = token or ""
        self._token_file = token_file
        self._owner = bool(owner)

    # --- token handling -------------------------------------------------

    def _adopt_rotation(self, reply: Dict[str, Any]) -> None:
        """Store a rotated token from a reply; the owner also persists it."""
        rotated = reply.get("token") if isinstance(reply, dict) else None
        if not rotated or rotated == self._token:
            return
        self._token = rotated
        if self._owner and self._token_file:
            try:
                from bed.tools.auth import _write_token_file

                _write_token_file(self._token_file, rotated)
            except Exception as e:
                logger.warning("BedMessageClient: token write failed: %s", e)

    def _reload_token_from_file(self) -> bool:
        """Non-owner: pick up a token the owner rotated. True if it changed."""
        if not self._token_file:
            return False
        from bed.tools._token import read_token_file

        fresh = read_token_file(self._token_file)
        if fresh and fresh != self._token:
            self._token = fresh
            return True
        return False

    async def _send_authed(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Send a message op with the token. One retry on ``not_authenticated``.

        Non-owner: re-read the token file and retry once. Owner: the
        owner is the only writer, so a stale token here means the server
        rejected it; report that rather than rotating silently.
        """
        msg = dict(message)
        msg["token"] = self._token
        reply = await self._conn.send(msg)
        self._adopt_rotation(reply)
        if reply.get("code") == "not_authenticated" and not self._owner:
            if self._reload_token_from_file():
                msg["token"] = self._token
                reply = await self._conn.send(msg)
                self._adopt_rotation(reply)
        return reply

    async def subscribe(self, moniker: str) -> Dict[str, Any]:
        """Subscribe to live message pushes for ``moniker``.

        Sends ``message_subscribe`` to bed and starts a background
        recv loop on the shared connection. The recv loop updates
        :func:`bbsengine6.message.set_local_unread_count` on every
        push.
        """
        from bbsengine6 import message as message_module

        moniker = (moniker or "").strip()
        if not moniker:
            return {
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
            }

        if self._subscribed_moniker == moniker and self._handler is not None:
            return {"ok": True, "moniker": moniker, "already_subscribed": True}

        try:
            reply = await self._send_authed(
                {"type": "message_subscribe", "moniker": moniker}
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}

        if not reply.get("ok"):
            return reply

        def _push(msg: Dict[str, Any]) -> None:
            if msg.get("type") != "message":
                return
            if msg.get("recipient_moniker") != moniker:
                return
            # The NOTIFY payload carries only the NEW status, not the old
            # one, so a delta cannot be computed from the push alone
            # (pending->delivered and a fresh delivered insert look the
            # same). Treat the push as a signal to recount, and write only
            # an exact DB count. This cannot drift and tolerates a missed
            # or reordered push.
            try:
                message_module.set_local_unread_count(
                    moniker, _exact_unread(moniker)
                )
            except Exception as e:
                logger.warning(
                    "BedMessageClient: unread recount failed for %s: %s",
                    moniker,
                    e,
                )

        await self._conn.subscribe(_push)
        self._subscribed_moniker = moniker
        self._handler = _push

        try:
            pending = await self.list_pending(moniker)
            if pending.get("ok"):
                count = len(pending.get("messages", []))
                try:
                    message_module.set_local_unread_count(moniker, count)
                except Exception as e:
                    logger.warning(
                        "BedMessageClient: set_local_unread_count failed: %s", e
                    )
        except BedUnavailable:
            pass

        logger.info("BedMessageClient: subscribed moniker=%s", moniker)
        return {"ok": True, "moniker": moniker}

    async def unsubscribe(self, moniker: Optional[str] = None) -> Dict[str, Any]:
        """Unsubscribe from message pushes."""
        target = moniker or self._subscribed_moniker
        if not target:
            return {"ok": True, "moniker": None, "no_subscription": True}
        try:
            reply = await self._send_authed(
                {"type": "message_unsubscribe", "moniker": target}
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if self._handler is not None:
            try:
                await self._conn.unsubscribe(self._handler)
            except Exception:
                pass
        if self._subscribed_moniker == target:
            self._subscribed_moniker = None
            self._handler = None
        return reply

    async def list_pending(
        self, moniker: Optional[str] = None
    ) -> Dict[str, Any]:
        """List pending messages for ``moniker`` from the server."""
        target = moniker or self._subscribed_moniker
        if not target:
            return {
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
                "messages": [],
            }
        try:
            reply = await self._send_authed(
                {"type": "message_list_pending", "moniker": target}
            )
        except BedUnavailable as e:
            return {
                "ok": False,
                "code": "bed_unavailable",
                "message": str(e),
                "messages": [],
            }
        return reply


_module_client: Optional[BedMessageServiceClient] = None


def get_message_client(
    connection: BedConnection,
    *,
    token: str = "",
    token_file: Optional[str] = None,
    owner: bool = False,
) -> BedMessageServiceClient:
    """Get or create the process-wide :class:`BedMessageServiceClient`.

    The cached client is rebuilt when the connection, token source, or
    ownership changes, so a caller never inherits another caller's token.
    """
    global _module_client
    c = _module_client
    if (
        c is None
        or c._conn is not connection
        or c._token_file != token_file
        or c._owner != bool(owner)
    ):
        _module_client = BedMessageServiceClient(
            connection, token=token, token_file=token_file, owner=owner
        )
    elif token and c._token != token:
        c._token = token
    return _module_client


def reset_message_client() -> None:
    """Drop the cached client (used in tests)."""
    global _module_client
    _module_client = None
