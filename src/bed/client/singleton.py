"""Module-level singleton wrapper for :class:`BedConnection`."""

from __future__ import annotations

import threading
import weakref
from typing import Any, Dict

from bed.client.connection import BedConnection

_CONNECTION_SINGLETON: Dict["weakref.ref[Any]", "BedConnection"] = {}
_CONNECTION_SINGLETON_LOCK = threading.Lock()


def get_bed_connection(args: Any) -> BedConnection:
    """Return the module-level :class:`BedConnection` for ``args``.

    One connection per ``args`` is cached via a weak reference, so a
    process holds at most one WebSocket to bed. The weakref ensures we
    never accidentally return a connection that was tied to a long-dead
    ``args`` object whose ``id`` has been recycled by the GC. Tests
    that want isolation should call
    :meth:`BedConnection.force_close` or use a fresh ``args``.
    """
    args_ref = weakref.ref(args)
    with _CONNECTION_SINGLETON_LOCK:
        for ref in list(_CONNECTION_SINGLETON):
            if ref() is None:
                _CONNECTION_SINGLETON.pop(ref, None)
        ref = args_ref
        if ref not in _CONNECTION_SINGLETON:
            _CONNECTION_SINGLETON[ref] = BedConnection(args)
        return _CONNECTION_SINGLETON[ref]


def reset_bed_connection(args: Any) -> None:
    """Drop the cached connection for ``args`` (test teardown helper)."""
    args_ref = weakref.ref(args)
    with _CONNECTION_SINGLETON_LOCK:
        conn = _CONNECTION_SINGLETON.pop(args_ref, None)
    if conn is not None:
        conn.force_close()
