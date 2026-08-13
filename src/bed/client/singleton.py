"""Module-level singleton wrapper for :class:`BedConnection`."""

from __future__ import annotations

import threading
import weakref
from typing import Any, Dict, Tuple

from bed.client.connection import BedConnection

_CONNECTION_SINGLETON: Dict[int, Tuple[weakref.ref[Any], BedConnection]] = {}
_CONNECTION_SINGLETON_LOCK = threading.Lock()


def get_bed_connection(args: Any) -> BedConnection:
    """Return the module-level :class:`BedConnection` for ``args``.

    One connection per ``args`` is cached via a weak reference, so a
    process holds at most one WebSocket to bed. The weakref ensures we
    never accidentally return a connection that was tied to a long-dead
    ``args`` object whose ``id`` has been recycled by the GC. Tests
    that want isolation should call
    :meth:`BedConnection.force_close` or use a fresh ``args``.

    The dict key is ``id(args)`` rather than the weakref itself: in
    Python 3.12+ ``weakref.ReferenceType.__hash__`` was changed to
    delegate to the referent, so non-hashable ``args`` (e.g. an
    ``argparse.Namespace``) would raise ``TypeError`` at lookup time.
    Storing ``id(args)`` keeps the lookup hashable while the value
    tuple carries the weakref we still need for GC sweeps.
    """
    args_id = id(args)
    with _CONNECTION_SINGLETON_LOCK:
        for stale_id in list(_CONNECTION_SINGLETON):
            ref, _conn = _CONNECTION_SINGLETON[stale_id]
            if ref() is None:
                _CONNECTION_SINGLETON.pop(stale_id, None)
        entry = _CONNECTION_SINGLETON.get(args_id)
        if entry is not None:
            ref, conn = entry
            if ref() is args:
                return conn
        ref = weakref.ref(args)
        conn = BedConnection(args)
        _CONNECTION_SINGLETON[args_id] = (ref, conn)
        return conn


def reset_bed_connection(args: Any) -> None:
    """Drop the cached connection for ``args`` (test teardown helper)."""
    with _CONNECTION_SINGLETON_LOCK:
        entry = _CONNECTION_SINGLETON.pop(id(args), None)
    if entry is not None:
        _ref, conn = entry
        conn.force_close()
