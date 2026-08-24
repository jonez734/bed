"""Send a ``ping`` to a running BED WebSocket, print the ``pong``, exit.

The connection layer is shared with :mod:`bbsengine6.net.ping` so the
``bedping`` shim, ``bbsengine6-ping``, ``casino-ping``, and
``zoid6-ping`` shims share one code path. :class:`PingUnavailable`
is re-exported from :mod:`bbsengine6.net.ping` so existing imports
(``bed.tools.ping.PingUnavailable``) keep working and the class
identity is preserved.

If the daemon is not listening, renders a one-line friendly message
via ``bbsengine6.io.echo(level="error")`` and returns ``1`` from
:func:`main` so the ``bin/bedping`` shim exits non-zero without a
Python traceback.

``bedping`` is intentionally credential-free: the daemon's
:class:`bed.api.ping.PingService` replies to ``{"type":"ping"}``
without an ``auth`` round-trip, so the client just sends the frame,
prints the ``pong`` envelope, and exits.
"""

from __future__ import annotations

import asyncio
import sys

from bbsengine6 import io

# Re-export for callers that import from ``bed.tools.ping``. Class
# identity is preserved: ``bed.tools.ping.PingUnavailable is
# bbsengine6.net.ping.PingUnavailable``.
from bbsengine6.net.ping import (  # noqa: F401
    PingUnavailable,
    connect,
    send_ping,
)


_PROG = "bedping"


def main() -> int:
    """CLI entry point invoked by ``bin/bedping``.

    Parses ``--host`` / ``--port`` / ``--path`` / ``--timeout`` via
    :func:`bbsengine6.net.ping.build_parser` (defaults: ``localhost``
    / ``8765`` / ``/`` / ``5.0``), runs :func:`send_ping`, prints the
    ``pong`` envelope on success, and on :class:`PingUnavailable`
    emits a friendly one-line error via :func:`bbsengine6.io.echo`
    with ``level="error"`` and returns ``1``.
    """
    from bbsengine6.net.ping import build_parser

    p = build_parser(prog=_PROG)
    args = p.parse_args()
    try:
        result = asyncio.run(
            send_ping(
                args.host,
                args.port,
                path=args.path,
                timeout=args.timeout,
                prog=_PROG,
            )
        )
    except PingUnavailable as exc:
        io.echo(str(exc), level="error")
        return 1
    # Protocol-level guard: the shared ``send_ping`` returns whatever
    # JSON the server replies with. A wrong ``type`` is a server-side
    # bug, not a transport failure, so it must propagate (visible to
    # the operator) rather than be silently swallowed into the
    # "connection refused" branch.
    assert result.get("type") == "pong", f"expected pong, got {result!r}"
    print(f"<- {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())