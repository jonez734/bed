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

    async def transfer(
        self,
        from_moniker: str,
        to_moniker: str,
        amount: int,
        requested_by: str,
    ) -> Dict[str, Any]:
        """Request a transfer from ``from_moniker`` to ``to_moniker``.

        Returns ``{"ok": True, "transfer_id": N, "message": "..."}`` on
        success and ``{"ok": False, "code": "...", "message": "..."}``
        on soft failure (``missing_moniker`` / ``invalid_amount`` /
        ``operation_failed`` / db errors).
        """
        from_moniker = (from_moniker or "").strip()
        to_moniker = (to_moniker or "").strip()
        if not from_moniker or not to_moniker:
            return {
                "ok": False,
                "code": "missing_moniker",
                "message": "from and to monikers are required",
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
                    "type": "bank_transfer_request",
                    "from": from_moniker,
                    "to": to_moniker,
                    "amount": amount,
                    "requested_by": requested_by,
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
            transfer_id = int(reply.get("transfer_id", 0))
        except (TypeError, ValueError):
            transfer_id = 0
        return {
            "ok": True,
            "from_moniker": from_moniker,
            "to_moniker": to_moniker,
            "amount": amount,
            "transfer_id": transfer_id,
            "message": reply.get("message", ""),
        }

    async def approve_transfer(
        self, transfer_id: int, responded_by: str
    ) -> Dict[str, Any]:
        """Approve a pending transfer.

        Returns ``{"ok": True, "transfer_id": N, "from_balance": N,
        "to_balance": N}`` on success and ``{"ok": False, "code": "...",
        "message": "..."}`` on soft failure.
        """
        try:
            transfer_id = int(transfer_id)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "transfer_id must be an integer",
            }
        if transfer_id <= 0:
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "transfer_id must be positive",
            }
        responded_by = (responded_by or "").strip()
        try:
            reply = await self._conn.send(
                {
                    "type": "bank_transfer_approve",
                    "transfer_id": transfer_id,
                    "responded_by": responded_by,
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
            from_balance = int(reply.get("from_balance", 0))
        except (TypeError, ValueError):
            from_balance = 0
        try:
            to_balance = int(reply.get("to_balance", 0))
        except (TypeError, ValueError):
            to_balance = 0
        return {
            "ok": True,
            "transfer_id": transfer_id,
            "from_balance": from_balance,
            "to_balance": to_balance,
        }

    async def reject_transfer(
        self, transfer_id: int, responded_by: str
    ) -> Dict[str, Any]:
        """Reject a pending transfer."""
        try:
            transfer_id = int(transfer_id)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "transfer_id must be an integer",
            }
        if transfer_id <= 0:
            return {
                "ok": False,
                "code": "invalid_amount",
                "message": "transfer_id must be positive",
            }
        responded_by = (responded_by or "").strip()
        try:
            reply = await self._conn.send(
                {
                    "type": "bank_transfer_reject",
                    "transfer_id": transfer_id,
                    "responded_by": responded_by,
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
        return {"ok": True, "transfer_id": transfer_id}

    async def get_pending_transfers(
        self, moniker: str = "", is_sysop: bool = False
    ) -> Dict[str, Any]:
        """List pending transfers visible to ``moniker``.

        Sysops see every pending transfer; non-sysops see only the
        ones they own or are involved in.
        """
        moniker = (moniker or "").strip()
        try:
            reply = await self._conn.send(
                {
                    "type": "bank_pending",
                    "moniker": moniker,
                    "is_sysop": bool(is_sysop),
                }
            )
        except BedUnavailable as e:
            return {
                "ok": False,
                "code": "bed_unavailable",
                "message": str(e),
                "transfers": [],
            }
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
                "transfers": [],
            }
        return {
            "ok": True,
            "moniker": moniker,
            "is_sysop": bool(is_sysop),
            "transfers": list(reply.get("transfers", [])),
        }

    async def list_all(self) -> Dict[str, Any]:
        """List every account with its balance.

        Returns ``{"ok": True, "accounts": [{"moniker": ..., "balance":
        N}, ...]}`` on success and ``{"ok": False, "code": "...",
        "message": "...", "accounts": []}`` on soft failure.
        """
        try:
            reply = await self._conn.send({"type": "bank_list_all"})
        except BedUnavailable as e:
            return {
                "ok": False,
                "code": "bed_unavailable",
                "message": str(e),
                "accounts": [],
            }
        if reply.get("type") == "error":
            return {
                "ok": False,
                "code": reply.get("code", "unknown"),
                "message": reply.get("message", ""),
                "accounts": [],
            }
        accounts = [
            {"moniker": row.get("moniker", ""), "balance": int(row.get("balance", 0))}
            for row in reply.get("accounts", [])
        ]
        return {"ok": True, "accounts": accounts}


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
