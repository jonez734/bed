"""Connect to a BED WebSocket, send a ping, prompt for a moniker, then exit.

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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from bbsengine6 import io

# Re-export for callers that import from ``bed.tools.ping``. Class
# identity is preserved: ``bed.tools.ping.PingUnavailable is
# bbsengine6.net.ping.PingUnavailable``.
from bbsengine6.net.ping import (  # noqa: F401
    PingUnavailable,
    connect,
    main as _helper_main,
    send_ping,
)


_PROG = "bedping"


async def _ping_then_auth(host: str, port: int) -> None:
    """Open the WS, send ping, prompt for moniker, send auth.

    Uses :func:`bbsengine6.net.ping.connect` so connection-level
    failures share the :class:`PingUnavailable` path with the
    generic helper. The ``prog="bedping"`` keyword flows into
    :class:`PingUnavailable` so the rendered error reads
    ``bedping: cannot connect to ws://host:port/ ...``.
    """
    ws = await connect(host, port, prog=_PROG)
    try:
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await ws.recv())
        assert pong.get("type") == "pong", f"expected pong, got {pong!r}"
        print(f"<- {pong}")
        moniker = io.inputstring(
            "{var:promptcolor}moniker: {var:inputcolor}", ""
        ).strip()
        auth = {"type": "auth", "moniker": moniker, "password": ""}
        await ws.send(json.dumps(auth))
        result = json.loads(await ws.recv())
        print(f"<- {result}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


def main() -> int:
    """CLI entry point invoked by ``bin/bedping``.

    Parses ``--host`` / ``--port`` (defaults: ``localhost`` / ``8765``),
    runs :func:`_ping_then_auth`, catches :class:`PingUnavailable`
    and emits a friendly one-line error via
    :func:`bbsengine6.io.echo` with ``level="error"``.
    """
    p = argparse.ArgumentParser(prog=_PROG)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    try:
        asyncio.run(_ping_then_auth(args.host, args.port))
    except PingUnavailable as exc:
        io.echo(str(exc), level="error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
