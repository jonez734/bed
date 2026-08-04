"""Client for the bed BankService.

A thin convenience wrapper around :class:`BedConnection` for the
``bank_balance`` / ``bank_add`` / ``bank_remove`` / ``bank_history``
wire types exposed by :class:`bed.api.bank.BankService`.

Unlike :class:`bed.client.messageservice.BedMessageServiceClient`,
this client does NOT maintain a server-side subscription — bank
operations are request/response only. The wrapper exists so callers
can write ``await client.get_balance("alice")`` instead of building
the dict + dispatching it through ``_request``.

Returns ``{"ok": False, "code": "...", "message": "..."}`` dicts on
soft failures (missing moniker, invalid amount, etc.) so the caller
can branch on ``code`` rather than catching :class:`BedUnavailable`.
Transport-level failures still raise :class:`BedUnavailable`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bed.client.connection import BedConnection
from bed.client.exceptions import BedUnavailable

logger = logging.getLogger(__name__)


class BedBankServiceClient:
    """Client for bed's BankService.

    Holds a :class:`BedConnection` and translates high-level bank
    operations (``get_balance`` / ``add_funds`` / ``remove_funds`` /
    ``get_history``) into the bank wire protocol. Soft failures
    (missing moniker, invalid amount, ledger error) come back as
    ``{"ok": False, "code": ..., "message": ...}`` dicts; transport
    failures (no connection, timeout) raise :class:`BedUnavailable`.
    """

    def __init__(self, connection: BedConnection) -> None:
        self._conn = connection

    async def get_balance(self, moniker: str) -> Dict[str, Any]:
        """Look up ``moniker``'s balance.

        Returns ``{"ok": True, "balance": N, "moniker": ...}`` on
        success and ``{"ok": False, "code": "...", "message": "..."}``
        on any soft failure (e.g. ``missing_moniker``).
        """
        moniker = (moniker or "").strip()
        if not moniker:
            return {
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
            }
        try:
            reply = await self._conn.send(
                {"type": "bank_balance", "moniker": moniker}
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
            }
        try:
            balance = int(reply.get("balance", 0))
        except (TypeError, ValueError):
            balance = 0
        return {"ok": True, "moniker": moniker, "balance": balance}

    async def add_funds(
        self,
        moniker: str,
        amount: int,
        description: str = "credit",
    ) -> Dict[str, Any]:
        """Credit ``amount`` to ``moniker``'s account.

        Returns ``{"ok": True, "new_balance": N, ...}`` on success and
        ``{"ok": False, "code": "...", "message": "..."}`` on soft
        failures (``missing_moniker`` / ``invalid_amount`` / db errors).
        """
        moniker = (moniker or "").strip()
        if not moniker:
            return {
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
            }
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "amount must be an integer",
            }
        if amount <= 0:
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "amount must be positive",
            }
        try:
            reply = await self._conn.send(
                {
                    "type": "bank_add",
                    "moniker": moniker,
                    "amount": amount,
                    "description": description,
                }
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
            }
        try:
            new_balance = int(reply.get("new_balance", 0))
        except (TypeError, ValueError):
            new_balance = 0
        return {
            "ok": True,
            "moniker": moniker,
            "amount": amount,
            "new_balance": new_balance,
        }

    async def remove_funds(
        self,
        moniker: str,
        amount: int,
        description: str = "debit",
    ) -> Dict[str, Any]:
        """Debit ``amount`` from ``moniker``'s account.

        Returns ``{"ok": True, "new_balance": N, ...}`` on success and
        ``{"ok": False, "code": "...", "message": "..."}`` on soft
        failures (``missing_moniker`` / ``invalid_amount`` /
        ``insufficient_funds`` / db errors).
        """
        moniker = (moniker or "").strip()
        if not moniker:
            return {
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
            }
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "amount must be an integer",
            }
        if amount <= 0:
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "amount must be positive",
            }
        try:
            reply = await self._conn.send(
                {
                    "type": "bank_remove",
                    "moniker": moniker,
                    "amount": amount,
                    "description": description,
                }
            )
        except BedUnavailable as e:
            return {"ok": False, "code": "bed_unavailable", "message": str(e)}
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
            }
        try:
            new_balance = int(reply.get("new_balance", 0))
        except (TypeError, ValueError):
            new_balance = 0
        return {
            "ok": True,
            "moniker": moniker,
            "amount": amount,
            "new_balance": new_balance,
        }

    async def get_history(
        self, moniker: str, limit: int = 50
    ) -> Dict[str, Any]:
        """Return up to ``limit`` recent transactions for ``moniker``.

        Returns ``{"ok": True, "transactions": [...], ...}`` on success
        and ``{"ok": False, "code": "...", "message": "...",
        "transactions": []}`` on soft failure.
        """
        moniker = (moniker or "").strip()
        if not moniker:
            return {
                "ok": False,
                "code": "missing_moniker",
                "message": "moniker is required",
                "transactions": [],
            }
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "limit must be an integer",
                "transactions": [],
            }
        if limit < 0:
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "limit must be non-negative",
                "transactions": [],
            }
        try:
            reply = await self._conn.send(
                {
                    "type": "bank_history",
                    "moniker": moniker,
                    "limit": limit,
                }
            )
        except BedUnavailable as e:
            return {
                "ok": False,
                "code": "bed_unavailable",
                "message": str(e),
                "transactions": [],
            }
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
                "transactions": [],
            }
        return {
            "ok": True,
            "moniker": moniker,
            "transactions": list(reply.get("transactions", [])),
        }


_module_client: Optional[BedBankServiceClient] = None


def get_bank_client(connection: BedConnection) -> BedBankServiceClient:
    """Get or create a process-wide :class:`BedBankServiceClient`.

    The cached client is keyed implicitly by ``connection`` identity:
    a new client is built when ``connection`` differs from the one
    the cached client was built with (mirrors
    :func:`bed.client.messageservice.get_message_client`).
    """
    global _module_client
    if _module_client is None or _module_client._conn is not connection:
        _module_client = BedBankServiceClient(connection)
    return _module_client


def reset_bank_client() -> None:
    """Drop the cached client (used in tests)."""
    global _module_client
    _module_client = None
