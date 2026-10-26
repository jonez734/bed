"""bed serves casino table operations through zoid6-owned SECURITY DEFINER functions.

bed holds no table privileges on casino tables. Each WebSocket message is authorised by the same
gates as bank (session, wire token, session token, shape), then bed passes only the SHA-256 digest
of the bearer token, the op name, and the arguments to the database:

    engine.bed_casino_read(token_hash, op, args)   list_tables, get_table
    engine.bed_casino_op(token_hash, op, args)     create, update, join, leave

The acting moniker and sysop status come from engine.__bed_token and the sysop flag inside the
database, never from the request. The database function's jsonb result is returned unchanged.
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict, Optional

from bbsengine6 import io

from .errors import CODE_DATABASE_ERROR, error_envelope
from .handler import BaseService

# WebSocket type -> (database function, op name)
_TYPE_TO_CALL: Dict[str, tuple[str, str]] = {
    "casino_list_tables": ("bed_casino_read", "list_tables"),
    "casino_get_table": ("bed_casino_read", "get_table"),
    "casino_create_table": ("bed_casino_op", "create"),
    "casino_update_table": ("bed_casino_op", "update"),
    "casino_join_table": ("bed_casino_op", "join"),
    "casino_leave_table": ("bed_casino_op", "leave"),
}


class CasinoService(BaseService):
    """Casino table operations, authorised by bed and executed by the database."""

    HANDLED_TYPES = tuple(_TYPE_TO_CALL.keys())

    def __init__(
        self,
        args: Any,
        session_manager: Any,
        *,
        secret: Optional[bytes] = None,
        token_store: Any = None,
        instance_id: Optional[str] = None,
        clock: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(args, session_manager)
        self.token_store = token_store
        self.secret = bytes(secret) if secret else None
        self.instance_id = str(instance_id) if instance_id else None
        self._clock = clock
        self._pool = None

    def _check_access(self, websocket: Any, op: str, message: Dict[str, Any]) -> Any:
        """Authenticate a casino op with bank's session and token gates.

        The session, wire-token, session-token, and shape checks are reused from the bank
        service. The authorisation step is not: bbsengine6.bank.access knows bank verbs only.
        Casino's own rule (owner, sysop, hidden tables) is enforced inside engine.bed_casino_*,
        where the acting moniker is read from the verified token.
        """
        from . import bank as _bank

        state, err = _bank._get_or_bind_session_for(self, websocket, message)
        if err is not None:
            return None, err
        if "claims" not in message:
            claims, err = _bank._validate_wire_token(self, message)
            if err is not None:
                return state, err
            if claims is not None:
                message["claims"] = claims
            else:
                claims, err = _bank._validate_session_token(self, state)
                if err is not None:
                    return state, err
                if claims is not None:
                    message["claims"] = claims
        return state, None

    def _conn(self) -> Any:
        """A database connection as bed's own role. The functions are zoid6-owned."""
        from bbsengine6 import database

        return database.connect(self.args, pool=getattr(self, "_pool", None), set_role=False)

    def register_all(self, server: Any) -> None:
        server.register_service(self, list(self.HANDLED_TYPES))

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        call = _TYPE_TO_CALL.get(msg_type)
        if call is None:
            return None
        fn, op = call
        state, err = self._check_access(websocket, op, message)
        if err is not None:
            return err
        token = getattr(state, "auth_service_token", None) or ""
        args = {k: v for k, v in message.items() if k not in ("type", "token", "claims")}
        return self._call(fn, op, token, args, msg_type)

    def _call(self, fn: str, op: str, token: str, args: Dict[str, Any], msg_type: str) -> Dict[str, Any]:
        from bed.store.token_store import token_hash

        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT engine.{fn}(%s, %s, %s::jsonb)",
                    (token_hash(token), op, _json.dumps(args, default=str)),
                )
                row = cur.fetchone()
        except Exception as e:
            io.echo_traceback(f"bed.api.casino.{op}:")
            return error_envelope(CODE_DATABASE_ERROR, f"{op} failed: {e}")
        result = row[0] if row else None
        if isinstance(result, str):
            result = _json.loads(result)
        if not isinstance(result, dict):
            return error_envelope(CODE_DATABASE_ERROR, f"{op} returned nothing")
        return {"type": msg_type, **result}
