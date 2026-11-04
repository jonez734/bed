# bed/api/whiteboard.py
# WhiteboardService: bed-native server handler for the zoidoffice whiteboard wire messages.
#
# Wire types (request -> reply):
#   whiteboard_list     {"status": optional}          -> whiteboard_list   {"entries": [...]}
#   whiteboard_add      {"entry", "tags", "projectids", "status"}  -> whiteboard_add   {"id"}
#   whiteboard_edit     {"id", "entry", "status"}     -> whiteboard_edit   {"id"}
#   whiteboard_claim    {"id"}                        -> whiteboard_claim  {"id"}
#   whiteboard_unclaim  {"id"}                        -> whiteboard_unclaim {"id"}
#   whiteboard_rm       {"id"}                        -> whiteboard_rm     {"id"}
#
# bed holds no table privileges on the whiteboard tables. Each op calls engine.whiteboard_op with the
# SHA-256 digest of the bearer token; the acting moniker comes from the verified token row, never from
# the request. A refusal from the op is returned as the error envelope the op reported.

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from bbsengine6 import io

from .errors import (
    CODE_DATABASE_ERROR,
    check_claims_routable,
    error_envelope,
    unrouted_claim,
)
from .gate import _get_or_bind_session_for, _validate_session_token, _validate_wire_token
from .handler import BaseService

#: wire type -> (op name sent to engine.whiteboard_op, reply type)
_TYPE_TO_OP: Dict[str, Tuple[str, str]] = {
    "whiteboard_list": ("list", "whiteboard_list"),
    "whiteboard_add": ("add", "whiteboard_add"),
    "whiteboard_edit": ("edit", "whiteboard_edit"),
    "whiteboard_claim": ("claim", "whiteboard_claim"),
    "whiteboard_unclaim": ("unclaim", "whiteboard_unclaim"),
    "whiteboard_rm": ("rm", "whiteboard_rm"),
}


class WhiteboardService(BaseService):
    """Bed-native whiteboard service.

    Authenticates each message through the shared token gates (gate.py), then runs the
    operation through engine.whiteboard_op. The reply carries the op's result under the reply type.
    """

    HANDLED_TYPES = tuple(_TYPE_TO_OP.keys())

    def __init__(self, args: Any, session_manager: Any, *, secret: Optional[bytes] = None,
                 token_store: Any = None, instance_id: Optional[str] = None,
                 clock: Any = None, bed_name: Optional[str] = None) -> None:
        super().__init__(args, session_manager)
        self.secret = bytes(secret) if secret else None
        self.token_store = token_store
        self.instance_id = str(instance_id) if instance_id else None
        self._clock = clock
        self._bed_name = (bed_name or "").strip() or None

    def register_all(self, server: Any) -> None:
        check_claims_routable("WhiteboardService", self.HANDLED_TYPES, _TYPE_TO_OP)
        server.register_service(self, list(self.HANDLED_TYPES))

    async def handle_message(self, server: Any, websocket: Any, path: str,
                             message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msg_type = message.get("type")
        route = _TYPE_TO_OP.get(msg_type)
        if route is None:
            if msg_type in self.HANDLED_TYPES:
                return unrouted_claim("WhiteboardService", msg_type)
            return None
        op, reply_type = route
        return await self._run(websocket, message, op, reply_type)

    def _check_access(self, websocket: Any, message: Dict[str, Any]):
        """The gate order: session, wire token, then session token (see gate.py)."""
        state, err = _get_or_bind_session_for(self, websocket, message)
        if err is not None:
            return None, err
        claims, err = _validate_wire_token(self, message)
        if claims is None and err is None:
            _, err = _validate_session_token(self, state)
        if err is not None:
            return state, err
        return state, None

    async def _run(self, websocket: Any, message: Dict[str, Any], op: str, reply_type: str) -> Dict[str, Any]:
        state, err = self._check_access(websocket, message)
        if err is not None:
            return err
        token = (message.get("token") or getattr(state, "auth_service_token", None) or "")
        args = {k: v for k, v in message.items() if k not in ("type", "token", "request_id")}
        try:
            from bed.store.token_store import token_hash

            from bbsengine6 import database

            with database.connect(self.args, pool=getattr(self, "_pool", None), set_role=False) as conn:
                with database.cursor(conn) as cur:
                    import json as _json

                    cur.execute(
                        "SELECT engine.whiteboard_op(%s, %s, %s::jsonb)",
                        (token_hash(token), op, _json.dumps(args, default=str)),
                    )
                    row = cur.fetchone()
                conn.commit()
        except Exception as e:
            io.echo_traceback("bed.api.whiteboard._run:")
            return error_envelope(CODE_DATABASE_ERROR, f"whiteboard {op} failed: {e}")
        result = row[0] if row else None
        if isinstance(result, str):
            import json as _json

            result = _json.loads(result)
        result = result or {"success": False, "message": "whiteboard op returned nothing"}
        if not result.get("success"):
            return error_envelope(result.get("code", "whiteboard_error"), result.get("message", "refused"))
        payload = {k: v for k, v in result.items() if k != "success"}
        return {"type": reply_type, **payload}
