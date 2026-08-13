"""Shared routing helper for ``bed.tools.*`` scripts.

Two responsibilities:

- Register the ``--bed-*`` client flags + the ``--direct`` opt-out on a
  shared argparse parent so every tool under ``bed.tools`` exposes the
  same wiring knobs (matches the empyre convention in
  ``empyre.lib.buildargs``).

- Pick the backend at startup: ``"bed"`` (the WebSocket) by default,
  ``"direct"`` (the local DB via ``bbsengine6.bank.BankService``) when
  the operator passes ``--direct``. If ``--direct`` is not set and the
  bed daemon is unreachable on the configured host/port, raise
  :class:`BedNotReachable` so the tool exits non-zero with a clear
  message rather than silently splitting traffic.

Tools should call :func:`build_client_args` from their own
``buildargs`` and :func:`select_backend` after argparse parsing. The
tool is expected to catch :class:`BedNotReachable` and exit non-zero
with the bundled operator-facing message.
"""

from __future__ import annotations

import argparse
from typing import Literal

from bed.client.probe import probe_bed


Backend = Literal["bed", "direct"]


class BedNotReachable(Exception):
    """Raised by :func:`select_backend` when bed is unreachable and
    ``--direct`` was not requested.

    The CLI catches this in its ``main`` and exits non-zero with the
    bundled one-line operator-facing message rather than splitting
    traffic between two backends.
    """

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        super().__init__(
            f"bed unreachable at {host}:{port}; rerun with --direct"
        )


def build_client_args(parentparser: argparse.ArgumentParser) -> None:
    """Add the ``--bed-*`` + ``--direct`` flags used by every tool."""
    group = parentparser.add_argument_group("bed client options")
    group.add_argument(
        "--bed-host",
        dest="bed_host",
        default="localhost",
        help="bed WebSocket host (default: localhost)",
    )
    group.add_argument(
        "--bed-port",
        dest="bed_port",
        type=int,
        default=8765,
        help="bed WebSocket port (default: 8765)",
    )
    group.add_argument(
        "--bed-path",
        dest="bed_path",
        default="/",
        help="bed URL path (default: /)",
    )
    group.add_argument(
        "--bed-call-timeout",
        dest="bed_call_timeout",
        type=float,
        default=5.0,
        help="bed RPC timeout in seconds (default: 5.0)",
    )
    group.add_argument(
        "--bed-probe-timeout",
        dest="bed_probe_timeout",
        type=float,
        default=0.25,
        help="bed TCP probe timeout in seconds (default: 0.25)",
    )
    group.add_argument(
        "--direct",
        dest="direct",
        action="store_true",
        default=False,
        help=(
            "Talk to the local database directly via bbsengine6.bank "
            "instead of routing through the bed daemon. Use when bed "
            "is not running."
        ),
    )


def select_backend(args) -> Backend:
    """Pick the backend for ``args``.

    Returns ``"direct"`` when ``--direct`` is set, regardless of bed
    reachability (the probe is skipped). Returns ``"bed"`` when bed is
    reachable on the configured host/port. Raises
    :class:`BedNotReachable` when bed is unreachable and ``--direct``
    was not requested.
    """
    if getattr(args, "direct", False):
        return "direct"
    if probe_bed(args):
        return "bed"
    raise BedNotReachable(
        getattr(args, "bed_host", "localhost"),
        int(getattr(args, "bed_port", 8765)),
    )
