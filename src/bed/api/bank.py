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
#   Every handler delegates its access decision to
#   ``bbsengine6.bank.access(args, op, session=state, message=msg)``.
#   This module is the reference implementation for the
#   bbsengine6.<name>.access() pattern; other bed.api.* services
#   (auth, message, ...) should follow this template -- see the
#   TODO comments in bed/api/auth.py and bed/api/message.py.
#
#   Handlers perform three gates in order:
#     1. Session bound (else ``not_authenticated``).
#     2. Wire-shape validation -- moniker present, amount int > 0,
#        transfer_id int > 0 (else ``missing_moniker`` /
#        ``invalid_amount``). This stays in the handler because
#        envelope codes are a wire-protocol concern.
#     3. ``bbsengine6.bank.access()`` authorization (else ``forbidden``).
#   ``bank_list_all`` is sysop-only. ``bank_pending`` ignores the
#   wire's ``is_sysop`` and uses ``state.is_sysop`` instead, so a
#   non-sysop session cannot escalate by sending ``is_sysop: true``.

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional, Tuple

from bbsengine6 import io
from bbsengine6.bank import BankService as _BBSBankService
from bbsengine6.bank import access as _bank_access

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


# Map from WS ``type`` field to the domain verb understood by
# ``bbsengine6.bank.access``. The bank module owns the verb vocabulary;
# this dict is the only place bed-side code needs to maintain the
# translation.
_TYPE_TO_OP: Dict[str, str] = {
    "bank_balance": "balance",
    "bank_add": "add",
    "bank_remove": "remove",
    "bank_history": "history",
    "bank_transfer_request": "transfer",
    "bank_transfer_approve": "approve",
    "bank_transfer_reject": "reject",
    "bank_pending": "pending",
    "bank_list_all": "list_all",
}


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


def _get_session_for(
    self_ref: "BankService", websocket: Any
) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
    """Look up the SessionState bound to ``websocket``.

    Returns ``(state, None)`` on success or ``(None, error_envelope)``
    when no session is bound (the websocket has not completed
    ``auth``/``reconnect``/``auth_refresh``). Kept as a module-level
    helper (not a method) so it composes cleanly with the
    ``_validate_shape`` / ``access()`` pipeline.
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
    """Validate the wire-shape invariants ``bbsengine6.bank.access``
    intentionally does not check.

    Returns ``None`` on success or an error envelope on failure.
    Kept here so envelope codes stay a wire-protocol concern, not an
    authorization concern.
    """
    if op in ("balance", "add", "remove", "history", "pending"):
        moniker = (message.get("moniker") or "").strip()
        if not moniker:
            return error_envelope(
                CODE_MISSING_MONIKER, "moniker is required"
            )
    if op in ("add", "remove"):
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
    if op == "history":
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
    if op == "transfer":
        f = (message.get("from") or "").strip()
        t = (message.get("to") or "").strip()
        if not f or not t:
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
    if op in ("approve", "reject"):
        try:
            tid = int(message.get("transfer_id", 0))
        except (TypeError, ValueError):
            return error_envelope(
                CODE_INVALID_AMOUNT, "transfer_id must be an integer"
            )
        if tid <= 0:
            return error_envelope(
                CODE_INVALID_AMOUNT, "transfer_id must be positive"
            )
    return None


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

    HANDLED_TYPES = tuple(_TYPE_TO_OP.keys())

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

    def _check_access(
        self, websocket: Any, op: str, message: Dict[str, Any]
    ) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
        """Run the three access gates in order: session, shape, authz.

        Returns ``(state, None)`` on success or ``(state_or_None, error_envelope)``
        on failure. The caller uses the returned envelope as the wire
        response and stops processing.
        """
        state, err = _get_session_for(self, websocket)
        if err is not None:
            return None, err

        err = _validate_shape(op, message)
        if err is not None:
            return state, err

        if not _bank_access(self.args, op, session=state, message=message):
            return state, forbidden("Operation not permitted for this account")

        return state, None

    def register_all(self, server: Any) -> None:
        server.register_service(self, list(self.HANDLED_TYPES))

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        op = _TYPE_TO_OP.get(msg_type)
        if op is None:
            return None
        handler = _OP_TO_HANDLER[op]
        return await handler(self, websocket, message)

    async def _handle_balance(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._check_access(websocket, "balance", message)
        if err is not None:
            return err
        moniker = (message.get("moniker") or "").strip()
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
        state, err = self._check_access(websocket, "add", message)
        if err is not None:
            return err
        moniker = (message.get("moniker") or "").strip()
        amount = int(message.get("amount", 0))
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
        state, err = self._check_access(websocket, "remove", message)
        if err is not None:
            return err
        moniker = (message.get("moniker") or "").strip()
        amount = int(message.get("amount", 0))
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
        state, err = self._check_access(websocket, "history", message)
        if err is not None:
            return err
        moniker = (message.get("moniker") or "").strip()
        limit = int(message.get("limit", 50))
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
        state, err = self._check_access(websocket, "transfer", message)
        if err is not None:
            return err
        from_moniker = (message.get("from") or "").strip()
        to_moniker = (message.get("to") or "").strip()
        amount = int(message.get("amount", 0))
        requested_by = (
            (message.get("requested_by") or "").strip() or state.moniker
        )
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
        state, err = self._check_access(websocket, "approve", message)
        if err is not None:
            return err
        transfer_id = int(message.get("transfer_id", 0))
        responded_by = (
            (message.get("responded_by") or "").strip() or state.moniker
        )
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
        state, err = self._check_access(websocket, "reject", message)
        if err is not None:
            return err
        transfer_id = int(message.get("transfer_id", 0))
        responded_by = (
            (message.get("responded_by") or "").strip() or state.moniker
        )
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
        state, err = self._check_access(websocket, "pending", message)
        if err is not None:
            return err
        moniker = (message.get("moniker") or "").strip()
        # Use server-side is_sysop from the session, not the wire's
        # ``is_sysop`` field, so a non-sysop session cannot escalate.
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
        state, err = self._check_access(websocket, "list_all", message)
        if err is not None:
            return err
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


# Domain-verb -> handler dispatch. Keeps handle_message() a flat
# dict lookup and makes it obvious at import time that every op has
# exactly one handler.
_OP_TO_HANDLER = {
    "balance": BankService._handle_balance,
    "add": BankService._handle_add,
    "remove": BankService._handle_remove,
    "history": BankService._handle_history,
    "transfer": BankService._handle_transfer_request,
    "approve": BankService._handle_transfer_approve,
    "reject": BankService._handle_transfer_reject,
    "pending": BankService._handle_pending,
    "list_all": BankService._handle_list_all,
}
