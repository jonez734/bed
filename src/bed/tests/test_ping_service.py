#!/usr/bin/env python3
# bed/tests/test_ping_service.py
# Unit tests for bed.api.ping.PingService: identity reply for ``ping``.

import argparse
import asyncio
import json
import sys
import unittest
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import websockets

sys.path.insert(0, "/home/opencode/data/work/bed/src")
sys.path.insert(0, "/home/opencode/data/work/bbsengine6/py/src")


from bed.api import PingService, SessionRegistry
from bbsengine6.session import SessionManager


class TestPingServiceHandle(unittest.IsolatedAsyncioTestCase):
    """PingService.handle_message returns the canonical pong shape
    with name + version."""

    def _service(self, name: str) -> PingService:
        args = argparse.Namespace()
        return PingService(args, SessionManager(), name=name)

    async def test_pong_includes_type_name_version(self):
        """The reply always has type=pong, the configured name, and
        the bed __version__."""
        from bed import _version

        svc = self._service("mybbs")
        reply = await svc.handle_message(
            server=None,
            websocket=None,
            path="/",
            message={"type": "ping"},
        )
        self.assertIsNotNone(reply)
        self.assertEqual(reply["type"], "pong")
        self.assertEqual(reply["name"], "mybbs")
        self.assertEqual(reply["version"], _version.__version__)

    async def test_pong_echoes_client_timestamp(self):
        """A client-supplied timestamp is echoed so probes can measure
        round-trip latency. Missing timestamp stays None (not 0)."""
        svc = self._service("bed")
        reply = await svc.handle_message(
            server=None,
            websocket=None,
            path="/",
            message={"type": "ping", "timestamp": 12345.678},
        )
        self.assertEqual(reply["timestamp"], 12345.678)

    async def test_pong_timestamp_is_none_when_absent(self):
        """No client timestamp -> pong.timestamp is None (not 0)."""
        svc = self._service("bed")
        reply = await svc.handle_message(
            server=None,
            websocket=None,
            path="/",
            message={"type": "ping"},
        )
        self.assertIsNone(reply["timestamp"])

    async def test_non_ping_message_returns_none(self):
        """PingService ignores non-ping messages; other services in the
        registry must answer them."""
        svc = self._service("bed")
        reply = await svc.handle_message(
            server=None,
            websocket=None,
            path="/",
            message={"type": "auth"},
        )
        self.assertIsNone(reply)

    async def test_empty_name_falls_back_to_bed(self):
        """A blank name in the constructor becomes 'bed' so the wire
        shape always has a non-empty identity field."""
        svc = PingService(argparse.Namespace(), SessionManager(), name="")
        reply = await svc.handle_message(
            server=None,
            websocket=None,
            path="/",
            message={"type": "ping"},
        )
        self.assertEqual(reply["name"], "bed")

    async def test_register_all_registers_ping(self):
        """register_all installs the service in the WebSocketServer's
        services dict under the 'ping' key."""
        from bbsengine6.net import WebSocketServer

        server = WebSocketServer(host="127.0.0.1", port=0)
        svc = self._service("bed")
        svc.register_all(server)
        self.assertIs(server.get_service("ping"), svc)


class TestPingServiceWinsOverRouter(unittest.IsolatedAsyncioTestCase):
    """BED.start() registers PingService LAST so its ``ping`` entry
    overwrites whatever the router registered first. bbsengine6 emits a
    WARNING on the overwrite; the swap is intentional."""

    async def test_last_writer_wins_for_ping(self):
        """When a router registers ``ping`` first and PingService
        registers ``ping`` second, get_service returns PingService."""
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter

        server = WebSocketServer(host="127.0.0.1", port=0)
        # Router first (its _handle_ping returns a plain pong).
        router = DefaultRouter(argparse.Namespace())
        router.register_all(server)
        # Bed's PingService second (its _handle_ping adds name/version).
        PingService(argparse.Namespace(), SessionRegistry(), "mybbs").register_all(server)

        svc = server.get_service("ping")
        self.assertIsInstance(svc, PingService)


class TestPingOverWire(unittest.IsolatedAsyncioTestCase):
    """End-to-end: a real WebSocket client sends ``ping`` to a server
    that has had a router's ``ping`` registration overwritten by
    PingService, and receives the enriched pong."""

    async def test_ping_pong_carries_name_and_version(self):
        import socket
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter

        # Pick a free ephemeral port so we don't collide with other tests.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        server = WebSocketServer(host="127.0.0.1", port=port)
        # The router's plain pong is registered first; bed's PingService
        # is registered second and wins.
        router = DefaultRouter(argparse.Namespace())
        router.register_all(server)
        ping_svc = PingService(
            argparse.Namespace(), SessionRegistry(), name="mybbs"
        )
        ping_svc.register_all(server)

        await server.start()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                await ws.send(json.dumps({"type": "ping", "timestamp": 42.0}))
                reply = json.loads(await ws.recv())
                self.assertEqual(reply["type"], "pong")
                self.assertEqual(reply["name"], "mybbs")
                self.assertIn("version", reply)
                self.assertEqual(reply["timestamp"], 42.0)
        finally:
            await server.stop()


if __name__ == "__main__":
    unittest.main()
