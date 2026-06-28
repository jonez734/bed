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
        mock_pool.connection.return_value.__enter__ = MagicMock(
            return_value=MagicMock()
        )
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

    def _parse_args(self):
        """Helper to parse args using buildargs."""
        import argparse
        from bed.main import buildargs

        parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
        buildargs(parser)
        return parser.parse_args()

    def test_default_port(self):
        """Test default port is 8765."""
        with patch("sys.argv", ["bed"]):
            args = self._parse_args()
            self.assertEqual(args.port, 8765)

    def test_default_host(self):
        """Test default host is 0.0.0.0."""
        with patch("sys.argv", ["bed"]):
            args = self._parse_args()
            self.assertEqual(args.host, "0.0.0.0")

    def test_custom_port(self):
        """Test custom port can be specified."""
        with patch("sys.argv", ["bed", "--port", "9999"]):
            args = self._parse_args()
            self.assertEqual(args.port, 9999)

    def test_custom_host(self):
        """Test custom host can be specified."""
        with patch("sys.argv", ["bed", "--host", "localhost"]):
            args = self._parse_args()
            self.assertEqual(args.host, "localhost")

    def test_default_router(self):
        """Test default router is DefaultRouter."""
        with patch("sys.argv", ["bed"]):
            args = self._parse_args()
            self.assertEqual(args.router, "bbsengine6.net.defaultrouter.DefaultRouter")

    def test_custom_router(self):
        """Test custom router can be specified."""
        with patch("sys.argv", ["bed", "--router", "mymodule.MyRouter"]):
            args = self._parse_args()
            self.assertEqual(args.router, "mymodule.MyRouter")

    def test_moniker_auth_router_resolves(self):
        """--router zoid6.api.handler.MonikerAuthRouter resolves via load_router_class."""
        from bed.main import load_router_class

        router_class = load_router_class("zoid6.api.handler.MonikerAuthRouter")
        self.assertTrue(callable(router_class))
        from zoid6.api.monikerrouter import MonikerAuthRouter
        self.assertIs(router_class, MonikerAuthRouter)


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


class TestConfigFlag(unittest.IsolatedAsyncioTestCase):
    """Test the --config CLI flag and the bind/database/autorestart merge."""

    def _parse(self, argv):
        import argparse
        from bed.main import buildargs

        parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
        buildargs(parser)
        with patch("sys.argv", ["bed"] + argv):
            return parser.parse_args()

    def test_config_flag_parses(self):
        """--config PATH populates args.config_file."""
        args = self._parse(["--config", "/tmp/bed.json"])
        self.assertEqual(args.config_file, "/tmp/bed.json")

    def test_config_flag_default_is_packaged_bed_json(self):
        """Omitting --config leaves args.config_file pointing at the packaged bed/data/bed.json."""
        from pathlib import Path
        from bed import config as bed_config

        args = self._parse([])
        expected = str(bed_config.get_package_data_path("bed.json"))
        self.assertEqual(args.config_file, expected)
        self.assertTrue(Path(args.config_file).exists())

    def test_config_file_overrides_autorestart(self):
        """External config's bed.autorestart=false is reflected via get_autorestart_config."""
        from bed.main import get_autorestart_config

        args = self._parse([])
        cfg = {"bed": {"autorestart": False, "restart_delay": 7, "max_restarts": 2}}
        autorestart, restart_delay, max_restarts = get_autorestart_config(args, cfg)
        self.assertFalse(autorestart)
        self.assertEqual(restart_delay, 7)
        self.assertEqual(max_restarts, 2)

    def test_cli_no_autorestart_wins_over_config(self):
        """--no-autorestart wins over a config file that says autorestart=true."""
        from bed.main import get_autorestart_config

        args = self._parse(["--no-autorestart"])
        cfg = {"bed": {"autorestart": True, "restart_delay": 5, "max_restarts": 10}}
        autorestart, _, _ = get_autorestart_config(args, cfg)
        self.assertFalse(autorestart)

    def test_config_file_bind_overrides_defaults(self):
        """bind.host/bind.port in the config fill in args when CLI omitted them."""
        from bed.main import _apply_bind_config

        args = self._parse([])
        cfg = {"bind": {"host": "127.0.0.1", "port": 9999}}
        _apply_bind_config(args, cfg)
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 9999)

    def test_cli_host_wins_over_config(self):
        """Explicit --host overrides a config file's bind.host."""
        from bed.main import _apply_bind_config

        args = self._parse(["--host", "1.2.3.4"])
        cfg = {"bind": {"host": "127.0.0.1", "port": 9999}}
        _apply_bind_config(args, cfg)
        self.assertEqual(args.host, "1.2.3.4")
        self.assertEqual(args.port, 9999)

    def test_config_file_database_overrides_defaults(self):
        """database.name in the config fills in args.databasename when CLI omitted it."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {"database": {"name": "zoid6prod", "host": "db.local", "port": 5433}}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasename, "zoid6prod")
        self.assertEqual(args.databasehost, "db.local")
        self.assertEqual(args.databaseport, 5433)

    def test_cli_database_overrides_config(self):
        """Explicit --databasename wins over a config file's database.name."""
        from bed.main import _apply_database_config

        args = self._parse(["--databasename", "otherdb"])
        cfg = {"database": {"name": "zoid6prod", "host": "db.local", "port": 5433}}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasename, "otherdb")
        self.assertEqual(args.databasehost, "db.local")
        self.assertEqual(args.databaseport, 5433)

    def test_config_file_missing_exits(self):
        """--config pointing at a missing file causes main_async to sys.exit(1)."""
        import importlib
        import os
        import tempfile
        from unittest.mock import patch

        bed_main = importlib.import_module("bed.main")
        tmp = tempfile.mkdtemp()
        missing = os.path.join(tmp, "does-not-exist.json")

        with (
            patch("sys.argv", ["bed", "--config", missing]),
            patch.object(bed_main, "load_router_class", return_value=MagicMock()),
        ):
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(bed_main.main_async())
            self.assertEqual(cm.exception.code, 1)

    def test_load_config_reads_external_file(self):
        """config.load_config reads an external file and merges it with defaults."""
        import os
        import tempfile

        from bed import config

        tmp = tempfile.mkdtemp()
        external = os.path.join(tmp, "ext.json")
        with open(external, "w") as f:
            f.write('{"bed": {"autorestart": false, "restart_delay": 9}}')
        cfg = config.load_config(external)
        self.assertIn("bed", cfg)
        self.assertFalse(cfg["bed"]["autorestart"])
        self.assertEqual(cfg["bed"]["restart_delay"], 9)
        # packaged defaults for non-overridden keys remain
        self.assertEqual(cfg["bed"]["max_restarts"], 10)


if __name__ == "__main__":
    unittest.main()
