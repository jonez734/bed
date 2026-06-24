#!/usr/bin/env python3
# bed/tests/test_bed.py
# Integration tests for BED (BBS Engine Daemon)

import argparse
import asyncio
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

import websockets

sys.path.insert(0, "/home/opencode/data/work/bed/src")


class TestBEDMocked(unittest.IsolatedAsyncioTestCase):
    """Test BED with mocked database."""

    async def asyncSetUp(self):
        """Start BED before each test."""
        from bbsengine6.net import WebSocketServer
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        self.mock_args = MagicMock()
        self.mock_args.databasename = "test"
        self.mock_args.databasehost = "localhost"
        self.mock_args.databaseport = 5432
        self.mock_args.databaseuser = "test"
        self.mock_args.databasepassword = "test"
        self.mock_args.debug = False
        self.mock_args.host = "127.0.0.1"
        self.mock_args.port = 18772

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)
        self.mock_args.pool = mock_pool

        self.bed = BED(self.mock_args, DefaultRouter)

        self.bed.server = WebSocketServer(
            host=self.mock_args.host,
            port=self.mock_args.port,
        )

        self.bed.router = DefaultRouter(self.mock_args)
        self.bed.router.register_all(self.bed.server)

        await self.bed.server.start()
        self._server_started = True

    async def asyncTearDown(self):
        """Stop BED after each test."""
        if hasattr(self, "_server_started") and self._server_started:
            await self.bed.server.stop()

    async def test_bed_starts(self):
        """Test BED starts successfully."""
        self.assertTrue(self.bed.server is not None)
        self.assertTrue(self.bed.server.is_running)

    async def test_connect_to_bed(self):
        """Test connecting to BED server."""
        uri = f"ws://{self.mock_args.host}:{self.mock_args.port}/"

        async with websockets.connect(uri) as ws:
            self.assertTrue(ws.state == websockets.State.OPEN)

    async def test_ping_pong(self):
        """Test ping/pong."""
        uri = f"ws://{self.mock_args.host}:{self.mock_args.port}/"

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            response = json.loads(await ws.recv())

            self.assertEqual(response["type"], "pong")

    async def test_list_services(self):
        """Test listing registered services."""
        services = self.bed.server.list_services()
        self.assertIsInstance(services, dict)


class TestBEDParseArgs(unittest.IsolatedAsyncioTestCase):
    """Test BED argument parsing."""

    def test_default_port(self):
        """Test default port is 8765."""
        with patch("sys.argv", ["bed"]):
            from bed.main import parse_args
            args = parse_args()
            self.assertEqual(args.port, 8765)

    def test_default_host(self):
        """Test default host is 0.0.0.0."""
        with patch("sys.argv", ["bed"]):
            from bed.main import parse_args
            args = parse_args()
            self.assertEqual(args.host, "0.0.0.0")

    def test_custom_port(self):
        """Test custom port can be specified."""
        with patch("sys.argv", ["bed", "--port", "9999"]):
            from bed.main import parse_args
            args = parse_args()
            self.assertEqual(args.port, 9999)

    def test_custom_host(self):
        """Test custom host can be specified."""
        with patch("sys.argv", ["bed", "--host", "localhost"]):
            from bed.main import parse_args
            args = parse_args()
            self.assertEqual(args.host, "localhost")

    def test_default_router(self):
        """Test default router is DefaultRouter."""
        with patch("sys.argv", ["bed"]):
            from bed.main import parse_args
            args = parse_args()
            self.assertEqual(args.router, "bbsengine6.net.defaultrouter.DefaultRouter")

    def test_custom_router(self):
        """Test custom router can be specified."""
        with patch("sys.argv", ["bed", "--router", "mymodule.MyRouter"]):
            from bed.main import parse_args
            args = parse_args()
            self.assertEqual(args.router, "mymodule.MyRouter")


class TestSessionManager(unittest.IsolatedAsyncioTestCase):
    """Test SessionManager class."""

    def test_register_session(self):
        """Test registering a session."""
        from bed.api.handler import SessionManager

        sm = SessionManager()
        sm.register_session(1, "testuser", True)

        session = sm.get_session(1)
        self.assertIsNotNone(session)
        self.assertEqual(session["moniker"], "testuser")
        self.assertTrue(session["is_sysop"])

    def test_unregister_session(self):
        """Test unregistering a session."""
        from bed.api.handler import SessionManager

        sm = SessionManager()
        sm.register_session(1, "testuser")
        sm.unregister_session(1)

        session = sm.get_session(1)
        self.assertIsNone(session)

    def test_get_moniker(self):
        """Test getting moniker from session."""
        from bed.api.handler import SessionManager

        sm = SessionManager()
        sm.register_session(1, "testuser")

        moniker = sm.get_moniker(1)
        self.assertEqual(moniker, "testuser")

    def test_get_moniker_not_found(self):
        """Test getting moniker from non-existent session."""
        from bed.api.handler import SessionManager

        sm = SessionManager()
        moniker = sm.get_moniker(999)
        self.assertIsNone(moniker)

    def test_get_is_sysop(self):
        """Test getting is_sysop flag."""
        from bed.api.handler import SessionManager

        sm = SessionManager()
        sm.register_session(1, "testuser", True)

        is_sysop = sm.get_is_sysop(1)
        self.assertTrue(is_sysop)

    def test_get_is_sysop_default(self):
        """Test getting is_sysop defaults to False."""
        from bed.api.handler import SessionManager

        sm = SessionManager()
        sm.register_session(1, "testuser")

        is_sysop = sm.get_is_sysop(1)
        self.assertFalse(is_sysop)


class TestBaseService(unittest.IsolatedAsyncioTestCase):
    """Test BaseService class."""

    def test_base_service_init(self):
        """Test BaseService initialization."""
        from bed.api.handler import BaseService, SessionManager

        args = argparse.Namespace(foo="bar")
        sm = SessionManager()
        service = BaseService(args, sm)

        self.assertEqual(service.args, args)
        self.assertEqual(service.sessions, sm)

    async def test_base_service_handle_message_not_implemented(self):
        """Test BaseService.handle_message raises NotImplementedError."""
        from bed.api.handler import BaseService, SessionManager

        args = argparse.Namespace(foo="bar")
        sm = SessionManager()
        service = BaseService(args, sm)

        with self.assertRaises(NotImplementedError):
            await service.handle_message(None, None, "/", {})


class TestConfig(unittest.IsolatedAsyncioTestCase):
    """Test config module."""

    def test_load_bed_defaults(self):
        """Test loading default config."""
        from bed import config

        cfg = config.load_bed_defaults()
        self.assertIn("bed", cfg)
        self.assertIn("debug", cfg)

    def test_load_config(self):
        """Test loading config."""
        from bed import config

        cfg = config.load_config()
        self.assertIn("bed", cfg)
        self.assertIn("debug", cfg)

    def test_reload_config(self):
        """Test reloading config."""
        from bed import config

        cfg = config.reload_config()
        self.assertIn("bed", cfg)


if __name__ == "__main__":
    unittest.main()
