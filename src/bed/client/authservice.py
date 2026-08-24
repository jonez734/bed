"""Client for the bed AuthService.

A thin convenience wrapper around :class:`BedConnection` for the four
``auth`` / ``reconnect`` / ``auth_refresh`` / ``auth_revoke`` wire types
exposed by :class:`bed.api.auth.AuthService`.

Mirrors :class:`bed.client.bankservice.BedBankServiceClient`:
- Empty inputs are rejected locally with a soft-failure dict and no
  transport call (so the caller can branch on ``code`` without catching
  :class:`BedUnavailable`).
- Server-side soft failures come back as ``{"ok": False, "code": "...",
  "message": "..."}`` so the caller can render a one-line error.
- Transport-level failures (no connection, timeout) are translated into
  ``{"ok": False, "code": "bed_unavailable", "message": "..."}``
  rather than re-raising :class:`BedUnavailable`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bed.client.connection import BedConnection
from bed.client.exceptions import BedUnavailable

logger = logging.getLogger(__name__)


class BedAuthServiceClient:
    """Client for bed's AuthService.

    Holds a :class:`BedConnection` and translates high-level auth
    operations (``login`` / ``reconnect`` / ``refresh`` / ``revoke``)
    into the auth wire protocol.
    """

    def __init__(self, connection: BedConnection) -> None:
        self._conn = connection

    async def login(self, moniker: str, password: str) -> Dict[str, Any]:
        """Authenticate with a ``(moniker, password)`` pair.

        Returns ``{"ok": True, "token": ..., "session_id": ...,
        "expires_at": ..., "moniker": ..., "is_sysop": bool, "balance":
        int}`` on success and ``{"ok": False, "code": "...",
        "message": "..."}`` on any soft failure.
        """
        moniker = (moniker or "").strip()
        password = password or ""
        if not moniker or not password:
            return {
                "ok": False,
                "code": "missing_credentials",
                "message": "moniker and password are required",
            }
        try:
            reply = await self._conn.send(
                {"type": "auth", "moniker": moniker, "password": password}
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
            }
        return {
            "ok": True,
            "moniker": reply.get("moniker", moniker),
            "is_sysop": bool(reply.get("is_sysop", False)),
            "session_id": reply.get("session_id", ""),
            "token": reply.get("token", ""),
            "expires_at": reply.get("expires_at", ""),
            "balance": int(reply.get("balance", 0) or 0),
        }

    async def reconnect(self, token: str) -> Dict[str, Any]:
        """Rebind an existing token to a new websocket.

        Returns ``{"ok": True, "token": ..., "session_id": ...,
        "expires_at": ..., "moniker": ..., "is_sysop": bool, "replayed":
        dict|None, "replayed_request_id": str|None}`` on success.
        """
        token = token or ""
        if not token:
            return {
                "ok": False,
                "code": "missing_token",
                "message": "token is required",
            }
        try:
            reply = await self._conn.send(
                {"type": "reconnect", "token": token}
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
            }
        env: Dict[str, Any] = {
            "ok": True,
            "moniker": reply.get("moniker", ""),
            "is_sysop": bool(reply.get("is_sysop", False)),
            "session_id": reply.get("session_id", ""),
            "token": reply.get("token", ""),
            "expires_at": reply.get("expires_at", ""),
            "balance": int(reply.get("balance", 0) or 0),
            "replayed": reply.get("replayed"),
        }
        if env["replayed"] is not None:
            env["replayed_request_id"] = reply.get("replayed_request_id")
        return env

    async def refresh(self, token: str) -> Dict[str, Any]:
        """Rotate a token on the original websocket.

        Returns the same shape as :meth:`login` (rotated token).
        The server returns ``not_authenticated`` if the call is made on
        a different websocket; the client surfaces that as a soft
        failure with ``code="not_authenticated"``.
        """
        token = token or ""
        if not token:
            return {
                "ok": False,
                "code": "missing_token",
                "message": "token is required",
            }
        try:
            reply = await self._conn.send(
                {"type": "auth_refresh", "token": token}
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
            }
        return {
            "ok": True,
            "moniker": reply.get("moniker", ""),
            "is_sysop": bool(reply.get("is_sysop", False)),
            "session_id": reply.get("session_id", ""),
            "token": reply.get("token", ""),
            "expires_at": reply.get("expires_at", ""),
            "balance": int(reply.get("balance", 0) or 0),
        }

    async def revoke(self, token: str) -> Dict[str, Any]:
        """Delete ``token`` from the bed store.

        Returns ``{"ok": True, "token": ..., "code": None|str}`` on
        success / soft failure. The wire envelope uses the
        ``auth_revoke_result`` shape (with a ``success`` flag, not
        ``ok``); we normalize it to ``ok`` so the caller branches on a
        single key.
        """
        token = token or ""
        if not token:
            return {
                "ok": False,
                "code": "missing_token",
                "message": "token is required",
            }
        try:
            reply = await self._conn.send(
                {"type": "auth_revoke", "token": token}
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
            }
        return {
            "ok": bool(reply.get("success")),
            "token": token,
            "code": reply.get("code"),
        }


_module_client: Optional[BedAuthServiceClient] = None


def get_auth_client(connection: BedConnection) -> BedAuthServiceClient:
    """Get or create a process-wide :class:`BedAuthServiceClient`.

    The cached client is keyed implicitly by ``connection`` identity:
    a new client is built when ``connection`` differs from the one
    the cached client was built with (mirrors
    :func:`bed.client.bankservice.get_bank_client`).
    """
    global _module_client
    if _module_client is None or _module_client._conn is not connection:
        _module_client = BedAuthServiceClient(connection)
    return _module_client


def reset_auth_client() -> None:
    """Drop the cached client (used in tests)."""
    global _module_client
    _module_client = None
