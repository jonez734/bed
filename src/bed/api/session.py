# bed/api/session.py
# Per-session registry: maps websocket <-> session_id, owns the monotonic
# request_id counter, and tracks the most recent un-acked IO request for
# replay-on-reconnect.

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


@dataclass
class SessionState:
    """All server-side state for one authenticated member.

    The `pending_request` field is the *last* IO request the server pushed
    to the client that has not yet been acked. On reconnect, the auth
    service replays exactly this envelope (the `request_id` survives the
    socket switch) and the client resumes with a single `echo_ack` /
    `*_reply` for that request_id.

    The casino-only ``table_moniker`` / ``spectator_of`` fields let the
    casino router reuse :class:`SessionRegistry` as its session store.
    ``table_moniker`` names the table the player is currently seated at
    (``None`` when not seated). ``spectator_of`` is the set of tables the
    session is currently spectating without being seated (multi-table
    watch is allowed; ``watch_table`` adds an entry, ``stop_watching``
    removes it).
    """
    session_id: str
    websocket_id: str
    moniker: str
    is_sysop: bool
    balance: Optional[int] = None
    request_id_counter: int = 0
    pending_request: Optional[Dict[str, Any]] = None
    auth_service_token: Optional[str] = None
    loginid: Optional[str] = None
    table_moniker: Optional[str] = None
    spectator_of: Set[str] = field(default_factory=set)


class SessionRegistry:
    """Thread-safe registry of SessionState, indexed two ways.

    The casino-only ``set_table_moniker`` / ``add_spectator`` /
    ``remove_spectator`` / ``get_table_observers`` /
    ``get_table_player_count`` methods maintain an indexed view of the
    per-table audience so a ``server.publish("casino:table:<X>", ...)``
    does not have to scan every session. The ``SessionState`` mirror
    fields are the source of truth -- the index is rebuilt from them
    on demand if it ever falls out of sync.
    """

    def __init__(self) -> None:
        self._by_session: Dict[str, SessionState] = {}
        self._by_websocket: Dict[str, SessionState] = {}
        self._table_observers: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()

    def bind(
        self,
        session_id: str,
        websocket_id: str,
        moniker: str,
        is_sysop: bool,
        *,
        balance: Optional[int] = None,
        loginid: Optional[str] = None,
        table_moniker: Optional[str] = None,
        spectator_of: Optional[Set[str]] = None,
    ) -> SessionState:
        with self._lock:
            old = self._by_websocket.pop(websocket_id, None)
            if old is not None and old.session_id != session_id:
                self._by_session.pop(old.session_id, None)
                self._purge_indices(old)

            state = self._by_session.get(session_id)
            if state is None:
                state = SessionState(
                    session_id=session_id,
                    websocket_id=websocket_id,
                    moniker=moniker,
                    is_sysop=is_sysop,
                    balance=balance,
                    loginid=loginid,
                    table_moniker=table_moniker,
                    spectator_of=set(spectator_of) if spectator_of else set(),
                )
                self._by_session[session_id] = state
            else:
                state.websocket_id = websocket_id
                state.moniker = moniker
                state.is_sysop = is_sysop
                if balance is not None:
                    state.balance = balance
                if loginid is not None:
                    state.loginid = loginid
                if table_moniker is not None:
                    state.table_moniker = table_moniker
                if spectator_of is not None:
                    state.spectator_of = set(spectator_of)
            self._by_websocket[websocket_id] = state
            self._reindex(state)
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
                self._purge_indices(state)

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

    def set_table_moniker(
        self, session_id: str, table_moniker: Optional[str]
    ) -> Optional[SessionState]:
        """Bind the session to ``table_moniker`` (or clear with ``None``).

        Returns the updated :class:`SessionState`, or ``None`` if the
        session is not registered. Replaces any previously bound table
        -- a player can only sit at one table at a time.
        """
        with self._lock:
            state = self._by_session.get(session_id)
            if state is None:
                return None
            state.table_moniker = table_moniker
            self._reindex(state)
            return state

    def get_table_moniker(self, session_id: str) -> Optional[str]:
        """Return the table the session is currently seated at, or ``None``."""
        with self._lock:
            state = self._by_session.get(session_id)
            return state.table_moniker if state is not None else None

    def add_spectator(self, session_id: str, table_moniker: str) -> Optional[SessionState]:
        """Mark ``session_id`` as spectating ``table_moniker``.

        Idempotent -- adding the same table twice is a no-op. Returns the
        updated :class:`SessionState`, or ``None`` if the session is
        not registered. The session can spectate multiple tables at once
        (one entry per ``watch_table`` call) and is also free to be
        seated at one table while spectating others.
        """
        with self._lock:
            state = self._by_session.get(session_id)
            if state is None:
                return None
            state.spectator_of.add(table_moniker)
            self._table_observers.setdefault(table_moniker, set()).add(session_id)
            return state

    def remove_spectator(
        self, session_id: str, table_moniker: str
    ) -> Optional[SessionState]:
        """Drop ``table_moniker`` from the session's spectator set.

        Idempotent -- removing an absent table is a no-op. Returns the
        updated :class:`SessionState`, or ``None`` if the session is
        not registered.
        """
        with self._lock:
            state = self._by_session.get(session_id)
            if state is None:
                return None
            state.spectator_of.discard(table_moniker)
            observers = self._table_observers.get(table_moniker)
            if observers is not None:
                observers.discard(session_id)
                if not observers:
                    self._table_observers.pop(table_moniker, None)
            return state

    def get_table_observers(self, table_moniker: str) -> Set[str]:
        """Return the set of session ids currently spectating ``table_moniker``."""
        with self._lock:
            return set(self._table_observers.get(table_moniker, set()))

    def get_table_player_count(self, table_moniker: str) -> int:
        """Return the number of sessions with ``state.table_moniker == table_moniker``."""
        with self._lock:
            return sum(
                1
                for state in self._by_session.values()
                if state.table_moniker == table_moniker
            )

    def _purge_indices(self, state: SessionState) -> None:
        """Remove ``state`` from the spectator index when the session is dropped."""
        for table in state.spectator_of:
            observers = self._table_observers.get(table)
            if observers is not None:
                observers.discard(state.session_id)
                if not observers:
                    self._table_observers.pop(table, None)

    def _reindex(self, state: SessionState) -> None:
        """Rebuild the observer-index entries for ``state`` from its current fields.

        Called after ``bind`` / ``set_table_moniker`` / drop so the
        table-keyed observer set matches the per-session truth. Cheap
        because the spectator set per session is bounded by the number
        of distinct tables a single connection watches.
        """
        for table in state.spectator_of:
            self._table_observers.setdefault(table, set()).add(state.session_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_session)
