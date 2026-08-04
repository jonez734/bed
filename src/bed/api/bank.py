# bed/api/bank.py
# BankService: native bed server-side handler for bank wire messages.
#
# This is the bed-native counterpart of ``bed.api.message.MessageService``.
# ``MessageService`` is a thin bed layer over
# ``bbsengine6.message.get_pending_messages`` and the LISTEN/NOTIFY fanout;
# ``BankService`` is the bed-native counterpart for bank operations,
# delegating actual ledger work to ``bbsengine6.bank.BankService``.
#
# Wire protocol (empyre shape, same as ``bed.client.bed.BedBankClient``):
#   Request:  {"type": "bank_balance",  "moniker": "<user>"}
#             {"type": "bank_add",      "moniker": ..., "amount": N, "description": str}
#             {"type": "bank_remove",   "moniker": ..., "amount": N, "description": str}
#             {"type": "bank_history",  "moniker": ..., "limit": N}
#   Response: {"type": "bank_balance",  "moniker": ..., "balance": N}
#             {"type": "bank_add",      "moniker": ..., "amount": N, "new_balance": N}
#             {"type": "bank_remove",   "moniker": ..., "amount": N, "new_balance": N}
#             {"type": "bank_history",  "moniker": ..., "transactions": [...]}
#   Error:    {"type": "error", "code": "...", "message": "..."}

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bbsengine6 import io
from bbsengine6.bank import BankService as _BBSBankService

from .errors import CODE_DATABASE_ERROR, error_envelope
from .handler import BaseService

logger = logging.getLogger(__name__)


CODE_MISSING_MONIKER = "missing_moniker"
CODE_INVALID_AMOUNT = "invalid_amount"


class BankService(BaseService):
    """Bed-native bank service.

    Wraps ``bbsengine6.bank.BankService`` (which owns the DB-backed
    account / transaction / transfer objects) and exposes the empyre
    wire shape (``bank_balance`` / ``bank_add`` / ``bank_remove`` /
    ``bank_history``) over a bed WebSocket session.

    The underlying :class:`bbsengine6.bank.BankService` is imported
    lazily on first message so the bed daemon can start without the
    bank package being importable (parallels :class:`MessageService`'s
    lazy ``psycopg`` import).
    """

    HANDLED_TYPES = (
        "bank_balance",
        "bank_add",
        "bank_remove",
        "bank_history",
    )

    def __init__(self, args: Any, session_manager: Any) -> None:
        super().__init__(args, session_manager)
        self._bank: Optional[Any] = None

    def _get_bank(self) -> Any:
        """Lazily construct the underlying bbsengine6 BankService.

        Construction is deferred to the first message so a transient
        DB outage at bed startup does not prevent the service from
        being registered (the connection is re-attempted on each
        call). The import itself is at module top so a missing
        ``bbsengine6.bank`` fails loudly at bed import time, matching
        the :class:`MessageService` convention.
        """
        if self._bank is None:
            self._bank = _BBSBankService(self.args)
        return self._bank

    def register_all(self, server: Any) -> None:
        server.register_service(self, list(self.HANDLED_TYPES))

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        if msg_type == "bank_balance":
            return await self._handle_balance(message)
        if msg_type == "bank_add":
            return await self._handle_add(message)
        if msg_type == "bank_remove":
            return await self._handle_remove(message)
        if msg_type == "bank_history":
            return await self._handle_history(message)
        return None

    @staticmethod
    def _require_moniker(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return None if ``moniker`` is present; else return an error envelope."""
        moniker = (message.get("moniker") or "").strip()
        if not moniker:
            return error_envelope(
                CODE_MISSING_MONIKER, "moniker is required"
            )
        return None

    async def _handle_balance(
        self, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        err = self._require_moniker(message)
        if err is not None:
            return err
        moniker = message["moniker"]
        try:
            balance = self._get_bank().get_balance(moniker)
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_balance:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"balance lookup failed: {e}"
            )
        return {
            "type": "bank_balance",
            "moniker": moniker,
            "balance": int(balance),
        }

    async def _handle_add(
        self, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        err = self._require_moniker(message)
        if err is not None:
            return err
        try:
            amount = int(message.get("amount", 0))
        except (TypeError, ValueError):
            return error_envelope(
                CODE_INVALID_AMOUNT, "amount must be an integer"
            )
        if amount <= 0:
            return error_envelope(
                CODE_INVALID_AMOUNT, "amount must be positive"
            )
        moniker = message["moniker"]
        description = (message.get("description") or "credit").strip() or "credit"
        try:
            result = self._get_bank().add_funds(
                moniker,
                amount,
                transaction_type="credit",
                description=description,
            )
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_add:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"add_funds failed: {e}"
            )
        if not result.get("success"):
            return error_envelope(
                CODE_DATABASE_ERROR,
                result.get("message", "add_funds failed"),
            )
        return {
            "type": "bank_add",
            "moniker": moniker,
            "amount": amount,
            "new_balance": int(result.get("new_balance", 0)),
        }

    async def _handle_remove(
        self, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        err = self._require_moniker(message)
        if err is not None:
            return err
        try:
            amount = int(message.get("amount", 0))
        except (TypeError, ValueError):
            return error_envelope(
                CODE_INVALID_AMOUNT, "amount must be an integer"
            )
        if amount <= 0:
            return error_envelope(
                CODE_INVALID_AMOUNT, "amount must be positive"
            )
        moniker = message["moniker"]
        description = (message.get("description") or "debit").strip() or "debit"
        try:
            result = self._get_bank().remove_funds(
                moniker,
                amount,
                transaction_type="debit",
                description=description,
            )
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_remove:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"remove_funds failed: {e}"
            )
        if not result.get("success"):
            return error_envelope(
                CODE_DATABASE_ERROR,
                result.get("message", "remove_funds failed"),
            )
        return {
            "type": "bank_remove",
            "moniker": moniker,
            "amount": amount,
            "new_balance": int(result.get("new_balance", 0)),
        }

    async def _handle_history(
        self, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        err = self._require_moniker(message)
        if err is not None:
            return err
        try:
            limit = int(message.get("limit", 50))
        except (TypeError, ValueError):
            return error_envelope(
                CODE_INVALID_AMOUNT, "limit must be an integer"
            )
        if limit < 0:
            return error_envelope(
                CODE_INVALID_AMOUNT, "limit must be non-negative"
            )
        moniker = message["moniker"]
        try:
            rows = self._get_bank().get_history(moniker, limit)
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_history:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"history lookup failed: {e}"
            )
        return {
            "type": "bank_history",
            "moniker": moniker,
            "transactions": list(rows),
        }
