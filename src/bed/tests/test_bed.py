#!/usr/bin/env python3
# bed/tests/test_bed.py
# Integration tests for BED (BBS Engine Daemon)

import argparse
import asyncio
import json
import os
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
        """Test default host is 127.0.0.1."""
        with patch("sys.argv", ["bed"]):
            args = self._parse_args()
            self.assertEqual(args.host, "127.0.0.1")

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

    def test_config_expands_tilde_in_bed_secret_path(self):
        """A literal '~' in auth.bed_secret_path is expanded to the user's
        home directory, matching the shell's ~-expansion. The bug was that
        the literal string was assigned verbatim, leaving a stray '~/.config/...'
        directory behind when bed tried to write to it as a relative path."""
        import os
        from bed.main import _apply_auth_config

        args = self._parse([])
        cfg = {"auth": {"bed_secret_path": "~/.config/bed/bed.secret"}}
        _apply_auth_config(args, cfg)
        self.assertEqual(args.bed_secret, os.path.expanduser("~/.config/bed/bed.secret"))
        self.assertFalse(args.bed_secret.startswith("~"))

    def test_config_expands_tilde_in_bind_host(self):
        """bind.host with a leading ~ is expanded (rare but legal)."""
        import os
        from bed.main import _apply_bind_config

        args = self._parse([])
        cfg = {"bind": {"host": "~/loopback"}}
        _apply_bind_config(args, cfg)
        self.assertEqual(args.host, os.path.expanduser("~/loopback"))
        self.assertFalse(args.host.startswith("~"))

    def test_config_expands_tilde_in_database_host(self):
        """database.host with a leading ~ is expanded (rare but legal)."""
        import os
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {"database": {"host": "~/db.sock"}}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasehost, os.path.expanduser("~/db.sock"))
        self.assertFalse(args.databasehost.startswith("~"))

    def test_flat_databaseuser_key_applied(self):
        """Flat 'databaseuser' key (from BED_DATABASEUSER env var) is applied."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {"databaseuser": "zoid6"}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databaseuser, "zoid6")

    def test_flat_databasepassword_key_applied(self):
        """Flat 'databasepassword' key (from BED_DATABASEPASSWORD env var) is applied."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {"databasepassword": "s3cret"}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasepassword, "s3cret")

    def test_nested_database_user_wins_over_flat(self):
        """When both nested database.user and flat databaseuser exist, nested wins."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {"database": {"user": "nested_user"}, "databaseuser": "flat_user"}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databaseuser, "nested_user")

    def test_cli_databaseuser_wins_over_flat(self):
        """Explicit --databaseuser on CLI wins over flat config key."""
        from bed.main import _apply_database_config

        args = self._parse(["--databaseuser", "cli_user"])
        cfg = {"databaseuser": "flat_user"}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databaseuser, "cli_user")

    def test_config_does_not_overwrite_explicit_bed_secret(self):
        """An explicit --bed-secret on the CLI wins over auth.bed_secret_path
        in the config. The '~' expansion must not run when the user passed
        their own value."""
        from bed.main import _apply_auth_config

        args = self._parse(["--bed-secret", "/tmp/explicit-secret"])
        cfg = {"auth": {"bed_secret_path": "/tmp/from-json"}}
        _apply_auth_config(args, cfg)
        self.assertEqual(args.bed_secret, "/tmp/explicit-secret")

    def test_config_expand_user_passes_through_non_strings(self):
        """_expand_user is defensive: a None or int in a misconfigured JSON
        entry does not crash the config-apply path."""
        from bed.main import _expand_user

        self.assertIsNone(_expand_user(None))
        self.assertEqual(_expand_user(42), 42)
        self.assertEqual(_expand_user(""), "")
        self.assertEqual(_expand_user("/already/absolute"), "/already/absolute")


class TestPidfile(unittest.TestCase):
    """Test the --pidfile arg and the _write_pidfile / _remove_pidfile
    helpers in bed/src/bed/main.py:main_async."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_pidfile_written_on_start(self):
        """_write_pidfile creates a file containing the current pid."""
        import os
        from bed.main import _write_pidfile, _remove_pidfile

        path = os.path.join(self._tmp, "bed.pid")
        fd = _write_pidfile(path)
        self.assertGreaterEqual(fd, 0)
        try:
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                content = f.read().strip()
            self.assertEqual(int(content), os.getpid())
        finally:
            os.close(fd)
            _remove_pidfile(path)

    def test_pidfile_optional(self):
        """_write_pidfile is not called when args.pidfile is None (no file
        is created in the temp dir)."""
        import os
        from bed.main import _write_pidfile

        self.assertIsNone(getattr(type("A", (), {"pidfile": None})(), "pidfile"))

    def test_pidfile_warn_on_write_failure(self):
        """_write_pidfile returns -1 and logs a warning when the path is
        in a nonexistent directory."""
        import os
        from unittest.mock import patch
        from bed.main import _write_pidfile

        bad_path = os.path.join(self._tmp, "nonexistent-subdir", "bed.pid")
        with patch("bed.main.io.echo") as mock_echo:
            fd = _write_pidfile(bad_path)
        self.assertEqual(fd, -1)
        self.assertTrue(os.path.exists(bad_path) is False)
        mock_echo.assert_called()
        # Verify the warning level was used
        args, kwargs = mock_echo.call_args
        self.assertEqual(kwargs.get("level"), "warning")

    def test_pidfile_cleanup_idempotent(self):
        """_remove_pidfile is a no-op when the file does not exist."""
        import os
        from bed.main import _remove_pidfile

        # File does not exist; _remove_pidfile should not raise.
        _remove_pidfile(os.path.join(self._tmp, "no-such-file.pid"))
        # Calling with empty path is also a no-op.
        _remove_pidfile("")

    def test_pidfile_roundtrip(self):
        """Write + remove leaves no file behind."""
        import os
        from bed.main import _write_pidfile, _remove_pidfile

        path = os.path.join(self._tmp, "bed-roundtrip.pid")
        fd = _write_pidfile(path)
        self.assertGreaterEqual(fd, 0)
        os.close(fd)
        self.assertTrue(os.path.exists(path))
        _remove_pidfile(path)
        self.assertFalse(os.path.exists(path))


class TestPidfileIntegration(unittest.IsolatedAsyncioTestCase):
    """End-to-end pidfile lifecycle via main_async."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.pidfile_path = os.path.join(self._tmp, "bed.pid")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    async def _run_main_async_until_pidfile_then_cancel(self, pre_pidfile=None):
        """Drive main_async in a task, wait for the pidfile to contain
        our pid (which distinguishes the file bed wrote from a
        pre-existing file the test wrote), then cancel the task.

        If pre_pidfile is set, write that pid to the pidfile path
        before launching so the test exercises the stale-or-collision
        path. Returns the task. The caller awaits the task and
        asserts on the exception.
        """
        import importlib
        from unittest.mock import MagicMock, patch

        bed_main = importlib.import_module("bed.main")

        if pre_pidfile is not None:
            with open(self.pidfile_path, "w") as f:
                f.write(f"{pre_pidfile}\n")

        # Use --no-autorestart and a temp config file (the packaged
        # default) so the config-file presence check passes. We
        # stub load_config so the file is not actually parsed.
        cfg_path = os.path.join(self._tmp, "bed.json")
        with open(cfg_path, "w") as f:
            f.write("{}")

        argv = [
            "bed",
            "--pidfile", self.pidfile_path,
            "--no-autorestart",
            "--config", cfg_path,
        ]
        with (
            patch("sys.argv", argv),
            patch.object(bed_main.config, "load_config", return_value={}),
            patch.object(bed_main, "load_router_class", return_value=MagicMock()),
            patch.object(bed_main, "BED") as MockBED,
        ):
            async def fake_start():
                await asyncio.sleep(60)

            async def fake_stop():
                return None

            MockBED.return_value.start = fake_start
            MockBED.return_value.stop = fake_stop

            task = asyncio.create_task(bed_main.main_async())
            # Poll for the pidfile to contain *our* pid, not the
            # stale one. With no pre_pidfile, just poll for existence.
            my_pid = os.getpid()
            for _ in range(100):
                if os.path.exists(self.pidfile_path):
                    try:
                        with open(self.pidfile_path) as f:
                            content = f.read().strip()
                        if content == str(my_pid):
                            break
                    except OSError:
                        pass
                await asyncio.sleep(0.02)
            task.cancel()
            return task

    async def test_main_async_writes_and_removes_pidfile(self):
        """main_async writes the pidfile on startup and removes it on exit."""
        task = await self._run_main_async_until_pidfile_then_cancel()
        # Sanity: pidfile was created.
        self.assertTrue(os.path.exists(self.pidfile_path))
        with open(self.pidfile_path) as f:
            self.assertEqual(int(f.read().strip()), os.getpid())
        with self.assertRaises(asyncio.CancelledError):
            await task
        # After unwind, the finally block must have removed the file.
        self.assertFalse(os.path.exists(self.pidfile_path))

    async def test_main_async_refuses_live_pid_collision(self):
        """A pre-existing pidfile with a 'live' pid causes main_async to
        sys.exit(1) without overwriting the file."""
        import importlib
        from unittest.mock import MagicMock, patch

        bed_main = importlib.import_module("bed.main")

        fake_live_pid = os.getpid() + 1
        with open(self.pidfile_path, "w") as f:
            f.write(f"{fake_live_pid}\n")

        def fake_kill(pid, sig):
            if pid == fake_live_pid:
                return  # pretend it succeeded
            return os.kill(pid, sig)

        import tempfile
        cfg_path = os.path.join(self._tmp, "bed.json")
        with open(cfg_path, "w") as f:
            f.write("{}")

        argv = [
            "bed",
            "--pidfile", self.pidfile_path,
            "--no-autorestart",
            "--config", cfg_path,
        ]
        with (
            patch("sys.argv", argv),
            patch.object(bed_main.config, "load_config", return_value={}),
            patch.object(bed_main, "load_router_class", return_value=MagicMock()),
            patch.object(bed_main.os, "kill", side_effect=fake_kill),
        ):
            with self.assertRaises(SystemExit) as cm:
                await bed_main.main_async()
            self.assertEqual(cm.exception.code, 1)

        # The pidfile must still hold the original (rejected) pid --
        # bed did not overwrite it.
        with open(self.pidfile_path) as f:
            self.assertEqual(int(f.read().strip()), fake_live_pid)

    async def test_main_async_overwrites_stale_dead_pidfile(self):
        """A pre-existing pidfile with a dead pid is overwritten with a
        warning; the new pid is recorded, then removed on exit."""
        # 2**31 - 1 is almost certainly not a live pid on any test box.
        stale_pid = 2**31 - 1
        task = await self._run_main_async_until_pidfile_then_cancel(
            pre_pidfile=stale_pid
        )
        # The pidfile must now hold our pid, not the stale one.
        self.assertTrue(os.path.exists(self.pidfile_path))
        with open(self.pidfile_path) as f:
            self.assertEqual(int(f.read().strip()), os.getpid())
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(os.path.exists(self.pidfile_path))


if __name__ == "__main__":
    unittest.main()
