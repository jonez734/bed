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
#   Handlers perform five gates in order:
#     1. Session bound (else ``not_authenticated``).
#     2. Wire token valid (if present) -- decode + HMAC verify +
#        store check + expiry + instance match (else ``token_invalid``
#        / ``token_revoked`` / ``instance_mismatch`` /
#        ``token_expired``). The CLI sends the bearer token it read
#        from ``--token-file`` on every wire call; when present it
#        is preferred over the session-bound token because it is
#        more recently captured and catches the case where the
#        session-bound snapshot has been revoked since WS open. The
#        decoded claims are stashed on ``message["claims"]`` so
#        ``bbsengine6.bank.access()`` can prefer claim-derived
#        ``moniker`` / ``is_sysop`` over the in-memory session.
#     3. Session token valid (if wire token absent) -- same gates
#        as #2 but reads ``state.auth_service_token``. Skipped when
#        the wire token already populated ``message["claims"]``.
#     4. Wire-shape validation -- moniker present, amount int > 0,
#        transfer_id int > 0 (else ``missing_moniker`` /
#        ``invalid_amount``). This stays in the handler because
#        envelope codes are a wire-protocol concern.
#     5. ``bbsengine6.bank.access()`` authorization (else ``forbidden``).
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

from .auth import TokenError, _decode_token
from .errors import (
    unrouted_claim,
    check_claims_routable,
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


from .gate import (  # noqa: E402  (re-exported: casino and the tests import these from bank)
    _get_session_for,
    _get_or_bind_session_for,
    _validate_token_against_store,
    _validate_session_token,
    _validate_wire_token,
)


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

    def __init__(
        self,
        args: Any,
        session_manager: Any,
        *,
        secret: Optional[bytes] = None,
        token_store: Optional[TokenStore] = None,
        instance_id: Optional[str] = None,
        clock: Optional[Any] = None,
        bed_name: Optional[str] = None,
    ) -> None:
        """Construct a BankService.

        ``secret``, ``token_store``, and ``instance_id`` are the
        token-aware wiring that lets :meth:`_check_access` re-verify
        ``state.auth_service_token`` on every bank op. They are all
        optional; if any is missing, the service falls back to
        session-based authorization without token re-verification.
        ``clock`` is the same injectable time source used by
        :class:`AuthService` for deterministic expiry tests.
        """
        super().__init__(args, session_manager)
        self._bank: Optional[Any] = None
        self.secret = bytes(secret) if secret else None
        self.token_store = token_store
        self.instance_id = str(instance_id) if instance_id else None
        self._clock = clock
        # The daemon's configured name, carried in balance replies so clients can label them.
        self._bed_name = (bed_name or "").strip() or None

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

    def _bank_conn(self) -> Any:
        """A database connection for the bank definer functions (bed's own role)."""
        from bbsengine6 import database

        return database.connect(
            self.args, pool=getattr(self, "_pool", None), set_role=False
        )

    def _now(self) -> float:
        """Return the current UNIX timestamp, honoring ``clock`` if set."""
        if self._clock is not None:
            return float(self._clock())
        import time as _time

        return _time.time()

    def _check_access(
        self, websocket: Any, op: str, message: Dict[str, Any]
    ) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
        """Run the access gates in order: session, wire-token, session-token, shape, authz.

        Returns ``(state, None)`` on success or ``(state_or_None,
        error_envelope)`` on failure. The caller uses the returned
        envelope as the wire response and stops processing.

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

        if not _bank_access(self.args, op, session=state, message=message):
            return state, forbidden("Operation not permitted for this account")

        return state, None

    def register_all(self, server: Any) -> None:
        check_claims_routable("BankService", self.HANDLED_TYPES, _TYPE_TO_OP)
        server.register_service(self, list(self.HANDLED_TYPES))

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        op = _TYPE_TO_OP.get(msg_type)
        if op is None:
            if msg_type in self.HANDLED_TYPES:
                return unrouted_claim("BankService", msg_type)
            return None
        handler = _OP_TO_HANDLER[op]
        return await handler(self, websocket, message)

    def _bank_op(self, state: Any, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Run one bank operation through engine.bed_bank_op and return its result.

        The acting moniker and sysop status come from the verified token row inside the
        database. bed passes only the token digest, the op name and the arguments.
        """
        import json as _json

        from bed.store.token_store import token_hash

        token = getattr(state, "auth_service_token", None) or ""
        with self._bank_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT engine.bed_bank_op(%s, %s, %s::jsonb)",
                (token_hash(token), op, _json.dumps(args, default=str)),
            )
            row = cur.fetchone()
        result = row[0] if row else None
        if isinstance(result, str):
            result = _json.loads(result)
        return result or {"success": False, "message": "bank op returned nothing"}

    async def _handle_balance(
        self, websocket: Any, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        state, err = self._check_access(websocket, "balance", message)
        if err is not None:
            return err
        moniker = (message.get("moniker") or "").strip()
        token = getattr(state, "auth_service_token", None) or ""
        try:
            from bed.store.token_store import token_hash

            with self._bank_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT account_exists, balance FROM engine.bed_bank_balance(%s, %s)",
                    (token_hash(token), moniker),
                )
                row = cur.fetchone()
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_balance:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"balance lookup failed: {e}"
            )
        exists, balance = (row[0], row[1]) if row else (False, 0)
        return {
            "type": "bank_balance",
            "moniker": moniker,
            "balance": int(balance),
            "account": bool(exists),
            "bed_name": self._bed_name,
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
            result = self._bank_op(state, "add", {
                "moniker": moniker, "amount": amount, "description": description,
            })
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_add:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"add failed: {e}"
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
            result = self._bank_op(state, "remove", {
                "moniker": moniker, "amount": amount, "description": description,
            })
        except Exception as e:
            io.echo_traceback("bed.api.bank._handle_remove:")
            return error_envelope(
                CODE_DATABASE_ERROR, f"remove failed: {e}"
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
            rows = self._bank_op(state, "history", {
                "moniker": moniker, "limit": limit,
            }).get("rows", [])
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
            result = self._bank_op(state, "transfer_request", {
                "from_moniker": from_moniker,
                "to_moniker": to_moniker,
                "amount": amount,
                "requested_by": requested_by,
            })
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
            result = self._bank_op(state, "transfer_approve", {
                "transfer_id": transfer_id,
                "responded_by": responded_by,
            })
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
            result = self._bank_op(state, "transfer_reject", {
                "transfer_id": transfer_id,
                "responded_by": responded_by,
            })
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
            rows = self._bank_op(state, "pending", {
                "moniker": moniker, "is_sysop": is_sysop,
            }).get("rows", [])
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
            rows = self._bank_op(state, "list_all", {}).get("rows", [])
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
