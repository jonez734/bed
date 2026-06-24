from typing import Any, Dict, Optional


class SessionManager:
    """Manages WebSocket sessions and authentication state."""

    def __init__(self):
        self._sessions: Dict[int, Dict[str, Any]] = {}

    def register_session(self, session_id: int, moniker: str, is_sysop: bool = False) -> None:
        self._sessions[session_id] = {
            "moniker": moniker,
            "is_sysop": is_sysop,
        }

    def unregister_session(self, session_id: int) -> None:
        if session_id in self._sessions:
            del self._sessions[session_id]

    def get_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        return self._sessions.get(session_id)

    def get_moniker(self, session_id: int) -> Optional[str]:
        session = self._sessions.get(session_id)
        return session.get("moniker") if session else None

    def get_is_sysop(self, session_id: int) -> bool:
        session = self._sessions.get(session_id)
        return session.get("is_sysop", False) if session else False


class BaseService:
    """Base class for message handlers."""

    def __init__(self, args: Any, session_manager: SessionManager):
        self.args = args
        self.sessions = session_manager

    async def handle_message(
        self, server: Any, websocket: Any, path: str, message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
