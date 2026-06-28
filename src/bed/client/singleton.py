"""Module-level singleton wrapper for :class:`BedConnection`."""

from __future__ import annotations

import threading
from typing import Any, Dict

from bed.client.connection import BedConnection

_CONNECTION_SINGLETON: Dict[int, "BedConnection"] = {}
_CONNECTION_SINGLETON_LOCK = threading.Lock()


def get_bed_connection(args: Any) -> BedConnection:
    """Return the module-level :class:`BedConnection` for ``args``.

    One connection per ``id(args)`` is cached, so a process holds at
    most one WebSocket to bed. Tests that want isolation should call
    :meth:`BedConnection.force_close` or use a fresh ``args``.
    """
    key = id(args)
    with _CONNECTION_SINGLETON_LOCK:
        conn = _CONNECTION_SINGLETON.get(key)
        if conn is None:
            conn = BedConnection(args)
            _CONNECTION_SINGLETON[key] = conn
        return conn


def reset_bed_connection(args: Any) -> None:
    """Drop the cached connection for ``args`` (test teardown helper)."""
    key = id(args)
    with _CONNECTION_SINGLETON_LOCK:
        conn = _CONNECTION_SINGLETON.pop(key, None)
    if conn is not None:
        conn.force_close()
