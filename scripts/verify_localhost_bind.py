#!/usr/bin/env python3.12
"""Verify BED binds to all addresses when given --bind localhost:8765.

Drives ``bed.main.BED.start()`` in-process with a single ``localhost``
bind on port 8765 and asserts that ``bed.server._bound_addrs`` contains
both an IPv4 (127.0.0.1) and IPv6 (::1) listener — the dual-stack
behaviour documented in ``bed/SPEC.md`` § 6.6.

Mirrors the helper pattern in
``bed/src/bed/tests/test_bed.py::TestBindMultiStart`` but uses the
literal localhost name rather than two pre-resolved IP entries, so
the verification actually exercises the ``getaddrinfo(AF_UNSPEC)``
fan-out at transport layer.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")
sys.path.insert(0, "../bbsengine6/py/src")

from bbsengine6.net.defaultrouter import DefaultRouter  # noqa: E402
from bed.main import BED  # noqa: E402


def _make_args() -> MagicMock:
    args = MagicMock()
    args.databasename = "test"
    args.databasehost = "localhost"
    args.databaseport = 5432
    args.databaseuser = "test"
    args.databasepassword = "test"
    args.debug = False
    args.bed_name = "bed"
    args.config_file = "/dev/null"
    args.token_persistence = "none"
    args.credential_provider = "password"
    args.bed_secret = None
    args.bed_instance_id = None
    args.token_ttl = 900
    args.no_message_service = True
    args.no_bank_service = True
    args.bind = [("localhost", 8765)]
    args.binds = None
    args.host = "127.0.0.1"
    args.port = 0
    return args


def _make_mock_pool() -> MagicMock:
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__enter__ = MagicMock(
        return_value=MagicMock()
    )
    mock_pool.connection.return_value.__exit__ = MagicMock(
        return_value=False
    )
    return mock_pool


async def _run(bed: BED, mock_pool: MagicMock) -> None:
    with patch("bed.main.getpool", return_value=mock_pool):
        await bed.start()


async def main() -> int:
    args = _make_args()
    bed = BED(args, DefaultRouter)
    start_task = asyncio.create_task(_run(bed, _make_mock_pool()))

    try:
        for _ in range(200):
            if bed.server is not None and bed.server._bound_addrs:
                break
            await asyncio.sleep(0.01)
        else:
            await bed.stop()
            start_task.cancel()
            try:
                await start_task
            except (asyncio.CancelledError, Exception):
                pass
            raise AssertionError(
                f"server never finished bind; "
                f"_bound_addrs={bed.server._bound_addrs if bed.server else None}"
            )

        bound = bed.server._bound_addrs
        print(f"bound listeners ({len(bound)}):")
        for fam, host, port in bound:
            print(f"  - {fam} {host}:{port}")

        families = {fam for fam, _h, _p in bound}
        hosts = {host for _f, host, _p in bound}
        ports = {port for _f, _h, port in bound}

        if not ({"inet", "inet6"} <= families):
            raise AssertionError(
                f"expected both 'inet' and 'inet6' families, got {families}"
            )
        if "127.0.0.1" not in hosts:
            raise AssertionError(f"expected 127.0.0.1 in bound hosts, got {hosts}")
        if "::1" not in hosts:
            raise AssertionError(f"expected ::1 in bound hosts, got {hosts}")
        if ports != {8765}:
            raise AssertionError(f"expected port 8765 only, got {ports}")

        async def _ping(url: str, label: str) -> None:
            import websockets
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({"type": "ping"}))
                resp = json.loads(await ws.recv())
                if resp.get("type") != "pong":
                    raise AssertionError(f"{label}: unexpected reply {resp}")
                print(f"  - reachable: {url} -> pong")

        await _ping("ws://127.0.0.1:8765/", "IPv4")
        await _ping("ws://[::1]:8765/", "IPv6")

        print("OK: localhost:8765 bound both 127.0.0.1 and ::1 on port 8765")
        return 0
    finally:
        await bed.stop()
        try:
            await start_task
        except (asyncio.CancelledError, Exception):
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
