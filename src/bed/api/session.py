# bed/api/session.py
# Per-session registry: maps websocket <-> session_id, owns the monotonic
# request_id counter, and tracks the most recent un-acked IO request for
# replay-on-reconnect.

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SessionState:
    """All server-side state for one authenticated member.

    The `pending_request` field is the *last* IO request the server pushed
    to the client that has not yet been acked. On reconnect, the auth
    service replays exactly this envelope (the `request_id` survives the
    socket switch) and the client resumes with a single `echo_ack` /
    `*_reply` for that request_id.
    """
    session_id: str
    websocket_id: str
    moniker: str
    is_sysop: bool
    balance: Optional[int] = None
    request_id_counter: int = 0
    pending_request: Optional[Dict[str, Any]] = None
    auth_service_token: Optional[str] = None


class SessionRegistry:
    """Thread-safe registry of SessionState, indexed two ways."""

    def __init__(self) -> None:
        self._by_session: Dict[str, SessionState] = {}
        self._by_websocket: Dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def bind(
        self,
        session_id: str,
        websocket_id: str,
        moniker: str,
        is_sysop: bool,
        *,
        balance: Optional[int] = None,
    ) -> SessionState:
        with self._lock:
            old = self._by_websocket.pop(websocket_id, None)
            if old is not None and old.session_id != session_id:
                self._by_session.pop(old.session_id, None)

            state = self._by_session.get(session_id)
            if state is None:
                state = SessionState(
                    session_id=session_id,
                    websocket_id=websocket_id,
                    moniker=moniker,
                    is_sysop=is_sysop,
                    balance=balance,
                )
                self._by_session[session_id] = state
            else:
                state.websocket_id = websocket_id
                state.moniker = moniker
                state.is_sysop = is_sysop
                if balance is not None:
                    state.balance = balance
            self._by_websocket[websocket_id] = state
            return state

    def rebind_websocket(self, old_websocket_id: str, new_websocket_id: str) -> Optional[SessionState]:
        with self._lock:
            state = self._by_websocket.pop(old_websocket_id, None)
            if state is None:
                return None
            state.websocket_id = new_websocket_id
            self._by_websocket[new_websocket_id] = state
            return state

    def unbind_websocket(self, websocket_id: str) -> Optional[SessionState]:
        with self._lock:
            return self._by_websocket.pop(websocket_id, None)

    def drop(self, session_id: str) -> None:
        with self._lock:
            state = self._by_session.pop(session_id, None)
            if state is not None:
                self._by_websocket.pop(state.websocket_id, None)

    def get_by_session(self, session_id: str) -> Optional[SessionState]:
        with self._lock:
            return self._by_session.get(session_id)

    def get_by_websocket(self, websocket_id: str) -> Optional[SessionState]:
        with self._lock:
            return self._by_websocket.get(websocket_id)

    def next_request_id(self, session_id: str) -> str:
        with self._lock:
            state = self._by_session.get(session_id)
            if state is None:
                raise KeyError(session_id)
            state.request_id_counter += 1
            return f"r{state.request_id_counter}"

    def record_pending(self, session_id: str, envelope: Dict[str, Any]) -> None:
        with self._lock:
            state = self._by_session.get(session_id)
            if state is None:
                return
            state.pending_request = envelope

    def take_pending(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._by_session.get(session_id)
            if state is None:
                return None
            env = state.pending_request
            state.pending_request = None
            return env

    def clear_pending(self, session_id: str) -> None:
        with self._lock:
            state = self._by_session.get(session_id)
            if state is None:
                return
            state.pending_request = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_session)
