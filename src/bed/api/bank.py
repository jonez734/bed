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
#
# Authentication / authorization:
#   Every handler requires a SessionState bound to the websocket (set by
#   AuthService on a successful ``auth``/``reconnect``/``auth_refresh``).
#   Handlers that target a specific account require the session moniker
#   to match the message moniker (case-insensitive, after strip);
#   sessions with ``is_sysop=True`` bypass the ownership check.
#   ``bank_list_all`` is sysop-only. ``bank_pending`` ignores the wire's
#   ``is_sysop`` and uses ``state.is_sysop`` instead, so a non-sysop
#   session cannot escalate by sending ``is_sysop: true``.

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional, Tuple

from bbsengine6 import io
from bbsengine6.bank import BankService as _BBSBankService

from .errors import (
    CODE_DATABASE_ERROR,
    error_envelope,
    forbidden,
    not_authenticated,
)
from .handler import BaseService
from .session import SessionState

logger = logging.getLogger(__name__)


CODE_MISSING_MONIKER = "missing_moniker"
CODE_INVALID_AMOUNT = "invalid_amount"
CODE_OPERATION_FAILED = "operation_failed"


def _jsonable(value: Any) -> Any:
    """Coerce a single value into something ``json.dumps`` can encode.

    ``datetime.datetime`` / ``datetime.date`` -> ISO 8601 string;
    ``Decimal`` -> ``int`` (banks track integer cents); everything
    else passes through untouched. Used to make DB row dicts safe
    for the WebSocket JSON transport.
    """
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return int(value)
    except ImportError:  # pragma: no cover - decimal is stdlib
        pass
    return value


def _jsonable_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``row`` with every value coerced via :func:`_jsonable`."""
    return {key: _jsonable(value) for key, value in row.items()}


def _moniker_eq(a: str, b: str) -> bool:
    """Case-insensitive moniker equality after strip()."""
    return (a or "").strip().lower() == (b or "").strip().lower()


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
        "bank_transfer_request",
        "bank_transfer_approve",
        "bank_transfer_reject",
        "bank_pending",
        "bank_list_all",
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

    def _require_session(
        self, websocket: Any
    ) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
        """Look up the SessionState bound to ``websocket``.

        Returns ``(state, None)`` on success or ``(None, error_envelope)``
        when no session is bound (the websocket has not completed
        ``auth``/``reconnect``/``auth_refresh``).
        """
        if websocket is None:
            return None, not_authenticated()
        try:
            ws_id = str(websocket.id)
        except Exception:
            return None, not_authenticated()
        state = self.sessions.get_by_websocket(ws_id)
        if state is None:
            return None, not_authenticated()
        return state, None

    @staticmethod
    def _require_owner(
        state: SessionState, target_moniker: str
    ) -> Optional[Dict[str, Any]]:
        """Reject if ``target_moniker`` is not the session's own account.

        A session with ``is_sysop=True`` may act on any account; a
        non-sysop session must match its own moniker (case-insensitive,
        after strip). Missing target is treated as forbidden rather
        than silently accepted, so a malformed envelope never falls
        through to the underlying bbsengine6 call.
        """
        if state.is_sysop:
            return None
        if not (target_moniker or "").strip():
            return forbidden("moniker is required")
        if not _moniker_eq(state.moniker, target_moniker):
            return forbidden(
                "Cannot operate on another member's account"
            )
        return None

    def register_all(self, server: Any) -> None:
        server.register_service(self, list(self.HANDLED_TYPES))

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        if msg_type == "bank_balance":
            return await self._handle_balance(websocket, message)
        if msg_type == "bank_add":
            return await self._handle_add(websocket, message)
        if msg_type == "bank_remove":
            return await self._handle_remove(websocket, message)
        if msg_type == "bank_history":
            return await self._handle_history(websocket, message)
        if msg_type == "bank_transfer_request":
            return await self._handle_transfer_request(websocket, message)
        if msg_type == "bank_transfer_approve":
            return await self._handle_transfer_approve(websocket, message)
        if msg_type == "bank_transfer_reject":
            return await self._handle_transfer_reject(websocket, message)
        if msg_type == "bank_pending":
            return await self._handle_pending(websocket, message)
        if msg_type == "bank_list_all":
            return await self._handle_list_all(websocket, message)
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
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        err = self._require_moniker(message)
        if err is not None:
            return err
        err = self._require_owner(state, message["moniker"])
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
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        err = self._require_moniker(message)
        if err is not None:
            return err
        err = self._require_owner(state, message["moniker"])
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
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        err = self._require_moniker(message)
        if err is not None:
            return err
        err = self._require_owner(state, message["moniker"])
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
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        err = self._require_moniker(message)
        if err is not None:
            return err
        err = self._require_owner(state, message["moniker"])
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
            "transactions": [_jsonable_row(row) for row in rows],
        }

    async def _handle_transfer_request(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        from_moniker = (message.get("from") or "").strip()
        to_moniker = (message.get("to") or "").strip()
        if not from_moniker or not to_moniker:
            return error_envelope(
                CODE_MISSING_MONIKER, "from and to monikers are required"
            )
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
        requested_by_raw = (message.get("requested_by") or "").strip()
        if (
            requested_by_raw
            and not state.is_sysop
            and not _moniker_eq(state.moniker, requested_by_raw)
        ):
            return forbidden(
                "requested_by does not match authenticated session"
            )
        if not state.is_sysop and not _moniker_eq(
            state.moniker, from_moniker
        ):
            return forbidden(
                "Cannot request a transfer from another member's account"
            )
        requested_by = requested_by_raw or state.moniker
        try:
            result = self._get_bank().transfer(
                from_moniker, to_moniker, amount, requested_by
            )
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_transfer_request:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"transfer request failed: {e}"
            )
        if not result.get("success"):
            return error_envelope(
                CODE_OPERATION_FAILED,
                result.get("message", "transfer request failed"),
            )
        try:
            transfer_id = int(result.get("transfer_id", 0))
        except (TypeError, ValueError):
            transfer_id = 0
        return {
            "type": "bank_transfer_request",
            "transfer_id": transfer_id,
            "message": result.get("message", ""),
        }

    async def _handle_transfer_approve(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        try:
            transfer_id = int(message.get("transfer_id", 0))
        except (TypeError, ValueError):
            return error_envelope(
                CODE_INVALID_AMOUNT, "transfer_id must be an integer"
            )
        if transfer_id <= 0:
            return error_envelope(
                CODE_INVALID_AMOUNT, "transfer_id must be positive"
            )
        responded_by_raw = (message.get("responded_by") or "").strip()
        if (
            responded_by_raw
            and not state.is_sysop
            and not _moniker_eq(state.moniker, responded_by_raw)
        ):
            return forbidden(
                "responded_by does not match authenticated session"
            )
        responded_by = responded_by_raw or state.moniker
        try:
            result = self._get_bank().approve_transfer(
                transfer_id, responded_by
            )
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_transfer_approve:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"transfer approve failed: {e}"
            )
        if not result.get("success"):
            return error_envelope(
                CODE_OPERATION_FAILED,
                result.get("message", "transfer approve failed"),
            )
        try:
            from_balance = int(result.get("from_balance", 0))
        except (TypeError, ValueError):
            from_balance = 0
        try:
            to_balance = int(result.get("to_balance", 0))
        except (TypeError, ValueError):
            to_balance = 0
        return {
            "type": "bank_transfer_approve",
            "transfer_id": transfer_id,
            "from_balance": from_balance,
            "to_balance": to_balance,
        }

    async def _handle_transfer_reject(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        try:
            transfer_id = int(message.get("transfer_id", 0))
        except (TypeError, ValueError):
            return error_envelope(
                CODE_INVALID_AMOUNT, "transfer_id must be an integer"
            )
        if transfer_id <= 0:
            return error_envelope(
                CODE_INVALID_AMOUNT, "transfer_id must be positive"
            )
        responded_by_raw = (message.get("responded_by") or "").strip()
        if (
            responded_by_raw
            and not state.is_sysop
            and not _moniker_eq(state.moniker, responded_by_raw)
        ):
            return forbidden(
                "responded_by does not match authenticated session"
            )
        responded_by = responded_by_raw or state.moniker
        try:
            result = self._get_bank().reject_transfer(
                transfer_id, responded_by
            )
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_transfer_reject:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"transfer reject failed: {e}"
            )
        if not result.get("success"):
            return error_envelope(
                CODE_OPERATION_FAILED,
                result.get("message", "transfer reject failed"),
            )
        return {
            "type": "bank_transfer_reject",
            "transfer_id": transfer_id,
        }

    async def _handle_pending(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        moniker = (message.get("moniker") or "").strip()
        if not moniker:
            return error_envelope(
                CODE_MISSING_MONIKER, "moniker is required"
            )
        err = self._require_owner(state, moniker)
        if err is not None:
            return err
        is_sysop = bool(state.is_sysop)
        try:
            rows = self._get_bank().get_pending_transfers(
                moniker, is_sysop
            )
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_pending:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"pending lookup failed: {e}"
            )
        return {
            "type": "bank_pending",
            "moniker": moniker,
            "is_sysop": is_sysop,
            "transfers": [_jsonable_row(row) for row in rows],
        }

    async def _handle_list_all(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._require_session(websocket)
        if err is not None:
            return err
        if not state.is_sysop:
            return forbidden("bank_list_all is sysop-only")
        try:
            rows = self._get_bank().list_all()
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_list_all:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"list_all failed: {e}"
            )
        return {
            "type": "bank_list_all",
            "accounts": [
                {"moniker": row["moniker"], "balance": int(row["balance"])}
                for row in rows
            ],
        }
