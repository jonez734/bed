"""Synchronous TCP probe for the bed daemon."""

from __future__ import annotations

import socket
from typing import Any


def probe_bed(args: Any) -> bool:
    """Synchronous TCP probe for ``args.bed_host:args.bed_port``.

    Returns ``True`` on a successful ``connect()``, ``False`` on any
    error. Never raises. Uses ``args.bed_probe_timeout`` (default
    0.25s) as the socket timeout.
    """
    host = getattr(args, "bed_host", "localhost")
    port = int(getattr(args, "bed_port", 8765))
    timeout = float(getattr(args, "bed_probe_timeout", 0.25))

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout, socket.gaierror):
        return False
    except Exception:
        return False
