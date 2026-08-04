#!/usr/bin/env python3
# bed/tests/test_bed.py
# Integration tests for BED (BBS Engine Daemon)

import argparse
import asyncio
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import websockets
from typing import Any

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
        cfg = getattr(self, "_extra_args", [])
        return parser.parse_args(cfg)

    def test_default_port(self):
        """Test default port is 8765."""
        self._extra_args = ["--config", "/dev/null"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.port, 8765)

    def test_default_host(self):
        """Test default host is 127.0.0.1."""
        self._extra_args = ["--config", "/dev/null"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.host, "127.0.0.1")

    def test_custom_port(self):
        """Test custom port can be specified."""
        self._extra_args = ["--config", "/dev/null", "--port", "9999"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.port, 9999)

    def test_custom_host(self):
        """Test custom host can be specified."""
        self._extra_args = ["--config", "/dev/null", "--host", "localhost"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.host, "localhost")

    def test_default_router(self):
        """Test default router is DefaultRouter."""
        self._extra_args = ["--config", "/dev/null"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.router, "bbsengine6.net.defaultrouter.DefaultRouter")

    def test_custom_router(self):
        """Test custom router can be specified."""
        self._extra_args = ["--config", "/dev/null", "--router", "mymodule.MyRouter"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.router, "mymodule.MyRouter")

    def test_moniker_auth_router_resolves(self):
        """--router zoid6.api.handler.MonikerAuthRouter resolves via load_router_class."""
        from bed.main import load_router_class

        router_class = load_router_class("zoid6.api.handler.MonikerAuthRouter")
        self.assertTrue(callable(router_class))
        from zoid6.api.monikerrouter import MonikerAuthRouter
        self.assertIs(router_class, MonikerAuthRouter)

    def test_load_router_class_bad_fqcn_emits_traceback_and_exits(self):
        """A non-existent --router FQCN routes through bbsengine6.module.load,
        which calls io.echo_traceback and re-raises; main_async emits an
        exit message and exits 1."""
        import importlib
        import os
        import tempfile
        from unittest.mock import patch

        bed_main = importlib.import_module("bed.main")
        bed_config = importlib.import_module("bed.config")
        bed_io = importlib.import_module("bbsengine6.io")

        tmp = tempfile.mkdtemp()
        cfg_path = os.path.join(tmp, "bed.json")
        with open(cfg_path, "w") as f:
            f.write("{}")

        traceback_calls = []
        echo_calls = []

        def fake_echo_traceback(msg, level="error"):
            traceback_calls.append((msg, level))

        def fake_echo(msg, **kwargs):
            echo_calls.append((msg, kwargs))

        with (
            patch(
                "sys.argv",
                [
                    "bed",
                    "--config",
                    cfg_path,
                    "--router",
                    "this.module.does.not.exist.MyRouter",
                ],
            ),
            patch.object(bed_config, "load_config", return_value={}),
            patch.object(bed_main, "_apply_bind_config", lambda *a, **k: None),
            patch.object(
                bed_main, "_apply_database_config", lambda *a, **k: None
            ),
            patch.object(
                bed_main, "_apply_auth_config", lambda *a, **k: None
            ),
            patch.object(bed_io, "echo_traceback", fake_echo_traceback),
            patch.object(bed_io, "echo", fake_echo),
        ):
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(bed_main.main_async())
            self.assertEqual(cm.exception.code, 1)

        # bbsengine6.module.load emitted the traceback with the bad FQCN
        self.assertTrue(
            any(
                "this.module.does.not.exist" in msg
                for msg, _ in traceback_calls
            ),
            f"expected echo_traceback with the bad FQCN; "
            f"got {traceback_calls!r}",
        )
        # main_async emitted the human-readable exit message
        self.assertTrue(
            any(
                "BED exiting" in msg and kwargs.get("level") == "error"
                for msg, kwargs in echo_calls
            ),
            f"expected 'BED exiting' error-level echo; "
            f"got {echo_calls!r}",
        )


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

    def test_load_config(self):
        """Test loading config from explicit file."""
        import os
        import json
        import tempfile
        from bed import config

        tmp = tempfile.mkdtemp()
        cfg_path = os.path.join(tmp, "bed.json")
        with open(cfg_path, "w") as f:
            json.dump({"bed": {"autorestart": True}, "debug": False}, f)
        cfg = config.load_config(cfg_path)
        self.assertIn("bed", cfg)
        self.assertIn("debug", cfg)

    def test_load_config_expands_tilde_in_path_key(self):
        """A literal '~' in auth.bed_secret_path is expanded to the user's
        home directory. Regression test for the bug where a literal
        '~/.config/bed/bed.secret' in bed.json was not expanded and bed
        created a stray './~/.config/...' directory."""
        import json
        import os
        import tempfile
        from bed import config

        tmp = tempfile.mkdtemp()
        cfg_path = os.path.join(tmp, "bed.json")
        with open(cfg_path, "w") as f:
            json.dump(
                {"auth": {"bed_secret_path": "~/.config/bed/bed.secret"}}, f
            )
        cfg = config.load_config(cfg_path)
        self.assertEqual(
            cfg["auth"]["bed_secret_path"],
            os.path.expanduser("~/.config/bed/bed.secret"),
        )
        self.assertFalse(cfg["auth"]["bed_secret_path"].startswith("~"))

    def test_load_config_expands_env_var_in_path_key(self):
        """$VAR and ${VAR} in path-shaped JSON values are expanded."""
        import json
        import os
        import tempfile
        from bed import config

        os.environ["BED_TEST_HOME"] = "/tmp/bedcfg-test-home"
        try:
            tmp = tempfile.mkdtemp()
            cfg_path = os.path.join(tmp, "bed.json")
            with open(cfg_path, "w") as f:
                json.dump(
                    {
                        "auth": {
                            "bed_secret_path": "$BED_TEST_HOME/.config/bed/bed.secret"
                        }
                    },
                    f,
                )
            cfg = config.load_config(cfg_path)
            self.assertEqual(
                cfg["auth"]["bed_secret_path"],
                "/tmp/bedcfg-test-home/.config/bed/bed.secret",
            )
        finally:
            del os.environ["BED_TEST_HOME"]

    def test_load_config_does_not_treat_non_path_keys_as_paths(self):
        """Strings whose keys do not end in a path suffix pass through
        unchanged. Module names like 'bed.api.message', mode strings like
        'memory', and hostnames like '127.0.0.1' must NOT be turned into
        filesystem paths (e.g. '/cwd/bed.api.message')."""
        import json
        import os
        import tempfile
        from bed import config

        tmp = tempfile.mkdtemp()
        cfg_path = os.path.join(tmp, "bed.json")
        with open(cfg_path, "w") as f:
            json.dump(
                {
                    "message_service": {
                        "modulepath": "bed.api.message",
                        "description": "Push notifications",
                    },
                    "auth": {
                        "token_persistence": "memory",
                        "credential_provider": "password",
                    },
                    "bind": {"host": "127.0.0.1", "port": 8765},
                },
                f,
            )
        cfg = config.load_config(cfg_path)
        self.assertEqual(
            cfg["message_service"]["modulepath"], "bed.api.message"
        )
        self.assertEqual(
            cfg["message_service"]["description"], "Push notifications"
        )
        self.assertEqual(cfg["auth"]["token_persistence"], "memory")
        self.assertEqual(cfg["auth"]["credential_provider"], "password")
        self.assertEqual(cfg["bind"]["host"], "127.0.0.1")
        self.assertEqual(cfg["bind"]["port"], 8765)

    def test_load_config_expands_env_supplied_path(self):
        """A BED_AUTH_BED_SECRET_PATH env var (a path-shaped value) is
        expanded at load time."""
        import os
        import tempfile
        from bed import config

        os.environ["BED_AUTH_BED_SECRET_PATH"] = "~/env-supplied-secret"
        try:
            tmp = tempfile.mkdtemp()
            cfg_path = os.path.join(tmp, "bed.json")
            with open(cfg_path, "w") as f:
                f.write("{}")
            cfg = config.load_config(cfg_path)
            self.assertEqual(
                cfg["auth"]["bed_secret_path"],
                os.path.expanduser("~/env-supplied-secret"),
            )
        finally:
            del os.environ["BED_AUTH_BED_SECRET_PATH"]


class TestConfigFlag(unittest.IsolatedAsyncioTestCase):
    """Test the --config CLI flag and the bind/database/autorestart merge."""

    def _parse(self, argv):
        import argparse
        from bed.main import buildargs

        parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
        buildargs(parser)
        with patch("sys.argv", ["bed", "--config", "/dev/null"] + argv):
            return parser.parse_args()

    def test_config_flag_parses(self):
        """--config PATH populates args.config_file."""
        import argparse
        from bed.main import buildargs

        parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
        buildargs(parser)
        with patch("sys.argv", ["bed", "--config", "/tmp/bed.json"]):
            args = parser.parse_args()
        self.assertEqual(args.config_file, "/tmp/bed.json")

    def test_config_flag_required(self):
        """Omitting --config causes an error (required=True)."""
        import argparse
        from bed.main import buildargs

        parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
        buildargs(parser)
        with self.assertRaises(SystemExit):
            with patch("sys.argv", ["bed"]):
                parser.parse_args()

    def test_config_file_overrides_autorestart(self):
        """External config's bed.autorestart=false is reflected via get_autorestart_config."""
        from bed.main import get_autorestart_config

        args = self._parse([])
        cfg = {"bed": {"autorestart": False, "restart_delay": 7, "max_restarts": 2}}
        autorestart, restart_delay, max_restarts = get_autorestart_config(args, cfg)
        self.assertFalse(autorestart)
        self.assertEqual(restart_delay, 7)
        self.assertEqual(max_restarts, 2)

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
        """config.load_config reads an explicit config file."""
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

        # Use a temp config file (the packaged default) so the
        # config-file presence check passes. We stub load_config
        # so the file is not actually parsed.
        cfg_path = os.path.join(self._tmp, "bed.json")
        with open(cfg_path, "w") as f:
            f.write("{}")

        argv = [
            "bed",
            "--pidfile", self.pidfile_path,
            "--config", cfg_path,
        ]
        with (
            patch("sys.argv", argv),
            patch.object(bed_main.config, "load_config", return_value={}),
            patch.object(bed_main, "load_router_class", return_value=MagicMock()),
            patch.object(bed_main, "ensure_startup", return_value=True),
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
            "--config", cfg_path,
        ]
        with (
            patch("sys.argv", argv),
            patch.object(bed_main.config, "load_config", return_value={}),
            patch.object(bed_main, "load_router_class", return_value=MagicMock()),
            patch.object(bed_main, "ensure_startup", return_value=True),
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


class TestBedJsonModuleImports(unittest.TestCase):
    """Verify every enabled module in zoid6's bed.json is importable and
    exposes a MessageRouter with a register_all method."""

    _BED_JSON = Path(__file__).resolve().parent.parent.parent.parent.parent / "zoid6" / "src" / "zoid6" / "data" / "bed.json"
    # postoffice is enabled but its module is not yet importable; it is
    # marked "required": false in bed.json so a missing module does not
    # abort the daemon. This constant is the importability-test escape
    # hatch (the test never sees it fail at runtime).
    _KNOWN_UNIMPORTABLE = {"postoffice"}

    def _load_services(self):
        with open(self._BED_JSON) as f:
            cfg = json.load(f)
        return {
            name: svc
            for name, svc in cfg.get("services", {}).items()
            if isinstance(svc, dict)
            and svc.get("enabled")
            and svc.get("modulepath")
        }

    def test_all_enabled_modulepaths_are_importable(self):
        """Every enabled modulepath in bed.json can be imported."""
        from bbsengine6.module import is_importable

        services = self._load_services()
        failures = {}
        for name, svc in services.items():
            if name in self._KNOWN_UNIMPORTABLE:
                continue
            mp = svc["modulepath"]
            if not is_importable(mp):
                failures[name] = mp
        self.assertEqual(
            failures,
            {},
            f"Unimportable modulepaths: {failures}",
        )

    def test_all_enabled_modules_have_messagerouter(self):
        """Every enabled module in bed.json has a MessageRouter class."""
        services = self._load_services()
        failures = {}
        for name, svc in services.items():
            if name in self._KNOWN_UNIMPORTABLE:
                continue
            mp = svc["modulepath"]
            try:
                mod = importlib.import_module(mp)
            except ImportError as e:
                failures[name] = f"{mp} (import error: {e})"
                continue
            if not hasattr(mod, "MessageRouter"):
                failures[name] = f"{mp} (no MessageRouter class)"
                continue
            router_cls = mod.MessageRouter
            if not callable(getattr(router_cls, "register_all", None)):
                failures[name] = f"{mp}.MessageRouter (no register_all method)"
        self.assertEqual(
            failures,
            {},
            f"Modules missing MessageRouter/register_all: {failures}",
        )


class TestGenericApplyConfigSection(unittest.TestCase):
    """The three named apply_*_config helpers now delegate to one generic
    helper. Tests here exercise the generic directly so a future change
    can't silently regress the helpers."""

    def _parse(self, argv):
        import argparse
        from bed.main import buildargs

        parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
        buildargs(parser)
        with patch("sys.argv", ["bed", "--config", "/dev/null"] + argv):
            return parser.parse_args()

    def test_generic_skips_when_arg_already_set(self) -> None:
        from bed.main import _apply_config_section, BIND_FIELDS

        args = self._parse(["--host", "1.2.3.4", "--port", "9999"])
        cfg = {"bind": {"host": "9.9.9.9", "port": 7777}}
        _apply_config_section(args, cfg, "bind", BIND_FIELDS)
        self.assertEqual(args.host, "1.2.3.4")
        self.assertEqual(args.port, 9999)

    def test_generic_fills_when_arg_at_default(self) -> None:
        from bed.main import _apply_config_section, BIND_FIELDS

        args = self._parse([])
        cfg = {"bind": {"host": "127.0.0.1", "port": 9000}}
        _apply_config_section(args, cfg, "bind", BIND_FIELDS)
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 9000)

    def test_generic_expands_user_for_marked_fields(self) -> None:
        from bed.main import _apply_config_section, BIND_FIELDS

        args = self._parse([])
        cfg = {"bind": {"host": "~/loopback"}}
        _apply_config_section(args, cfg, "bind", BIND_FIELDS)
        self.assertFalse(args.host.startswith("~"))
        self.assertIn("/loopback", args.host)

    def test_generic_coerces_int(self) -> None:
        from bed.main import _apply_config_section, BIND_FIELDS

        args = self._parse([])
        cfg = {"bind": {"port": "1234"}}
        _apply_config_section(args, cfg, "bind", BIND_FIELDS)
        self.assertEqual(args.port, 1234)
        self.assertIsInstance(args.port, int)

    def test_generic_skips_missing_section(self) -> None:
        from bed.main import _apply_config_section, BIND_FIELDS

        args = self._parse([])
        # No 'bind' key at all.
        _apply_config_section(args, cfg={}, section_name="bind", fields=BIND_FIELDS)
        # Argparse defaults intact.
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)

    def test_generic_skips_non_dict_section(self) -> None:
        from bed.main import _apply_config_section, BIND_FIELDS

        args = self._parse([])
        _apply_config_section(
            args, cfg={"bind": "not-a-dict"}, section_name="bind", fields=BIND_FIELDS
        )
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)


class TestDiffConfigSection(unittest.TestCase):
    """SIGHUP-time diff helper: returns (cli_arg, new_value) pairs that
    differ between cfg and args. Does not require args to be at default."""

    def _make_args(self) -> argparse.Namespace:
        return argparse.Namespace(host="127.0.0.1", port=8765)

    def test_diff_returns_pair_when_cfg_differs(self) -> None:
        from bed.main import _diff_config_section, BIND_FIELDS

        args = self._make_args()
        diffs = _diff_config_section(
            args, {"bind": {"port": 9999}}, "bind", BIND_FIELDS
        )
        self.assertEqual(diffs, [("port", 9999)])

    def test_diff_empty_when_same(self) -> None:
        from bed.main import _diff_config_section, BIND_FIELDS

        args = self._make_args()
        diffs = _diff_config_section(
            args, {"bind": {"host": "127.0.0.1", "port": 8765}}, "bind", BIND_FIELDS
        )
        self.assertEqual(diffs, [])

    def test_diff_does_not_require_default_value(self) -> None:
        """Unlike _apply_config_section, _diff_config_section reports diffs
        even when args.<cli_arg> is not the argparse default. SIGHUP is
        operator-initiated — explicit values are candidates too."""
        from bed.main import _diff_config_section, BIND_FIELDS

        args = self._make_args()
        diffs = _diff_config_section(
            args, {"bind": {"host": "0.0.0.0"}}, "bind", BIND_FIELDS
        )
        self.assertEqual(diffs, [("host", "0.0.0.0")])

    def test_diff_skips_missing_key(self) -> None:
        from bed.main import _diff_config_section, BIND_FIELDS

        args = self._make_args()
        diffs = _diff_config_section(args, {"bind": {}}, "bind", BIND_FIELDS)
        self.assertEqual(diffs, [])


class TestPhase2BED(unittest.IsolatedAsyncioTestCase):
    """Phase 2 daemon-lifecycle fixes."""

    def _bed_args(self, **overrides: Any) -> argparse.Namespace:
        defaults = dict(
            host="127.0.0.1",
            port=0,
            debug=False,
            databasename="x",
            databasehost="x",
            databaseport=0,
            databaseuser="x",
            databasepassword="x",
            bed_secret="",
            token_ttl=900,
            token_persistence="memory",
            credential_provider="password",
            bed_instance_id=None,
            config_file="/dev/null",
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _fake_pool(self):
        """A connection-pool stand-in that yields a no-op context manager."""
        pool = MagicMock()

        class _CM:
            def __enter__(self_inner):
                return MagicMock()
            def __exit__(self_inner, *a):
                return False

        pool.connection.return_value = _CM()
        return pool

    async def test_start_raises_on_db_failure(self) -> None:
        """A DB connection failure raises from BED.start(), it does not
        silently return. The autorestart loop counts the failure."""
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        args = self._bed_args(token_persistence="none")

        with patch("bed.main.getpool", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                bed = BED(args, DefaultRouter)
                await bed.start()

    async def test_start_constructs_session_registry_before_server(self) -> None:
        """start() creates _session_registry BEFORE constructing the
        WebSocketServer so a failure in _start_auth cannot leak a
        half-wired server."""
        import importlib

        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        bed_main_mod = importlib.import_module("bed.main")

        args = self._bed_args(token_persistence="none")

        order: list = []

        class _SpyServer:
            def __init__(self, *a, **kw):
                order.append(("server_init",))
            async def start(self):
                order.append(("server_start",))
            async def stop(self):
                order.append(("server_stop",))
            def register_service(self, router, names):
                order.append(("register_service", tuple(names)))
            def list_services(self):
                return []  # for the post-start log

        with patch("bed.main.getpool", return_value=self._fake_pool()), \
             patch.object(bed_main_mod, "WebSocketServer", _SpyServer):
            bed = BED(args, DefaultRouter)
            task = asyncio.create_task(bed.start())
            for _ in range(50):
                if bed._running:
                    break
                await asyncio.sleep(0.01)
            bed._running = False
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await bed.stop()

        self.assertIsNotNone(bed._session_registry)
        self.assertIn(("server_init",), order)
        self.assertIn(("server_start",), order)

    async def test_cleanup_partial_start_runs_on_register_failure(self) -> None:
        """If service registration raises after server construction but
        before server.start(), _cleanup_partial_start tears down whatever
        was started."""
        from bbsengine6.net import WebSocketServer
        from bed.main import BED

        class _BadRouter:
            def __init__(self, *a, **kw):
                raise RuntimeError("router boom")
            def register_all(self, server):
                pass

        args = self._bed_args(token_persistence="none")

        with patch("bed.main.getpool", return_value=self._fake_pool()), \
             patch.object(WebSocketServer, "__init__", lambda *a, **kw: None), \
             patch.object(WebSocketServer, "stop", AsyncMock()):
            bed = BED(args, _BadRouter)
            with self.assertRaises(RuntimeError):
                await bed.start()
        self.assertIsNone(bed.server)
        self.assertIsNone(bed._message_listener_task)


class TestSighupReload(unittest.IsolatedAsyncioTestCase):
    """SIGHUP reload: apply live knobs, warn on structural changes."""

    def _bed(self, *, token_ttl=900, token_persistence="memory", **kwargs):
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        args = self._bed_args(
            token_ttl=token_ttl,
            token_persistence=token_persistence,
            **kwargs,
        )
        return BED(args, DefaultRouter)

    def _bed_args(self, **overrides: Any) -> argparse.Namespace:
        defaults = dict(
            host="127.0.0.1",
            port=0,
            debug=False,
            databasename="x",
            databasehost="x",
            databaseport=0,
            databaseuser="x",
            databasepassword="x",
            bed_secret="",
            token_ttl=900,
            token_persistence="memory",
            credential_provider="password",
            bed_instance_id=None,
            config_file="/dev/null",
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_sighup_applies_token_ttl_to_auth_service(self) -> None:
        """SIGHUP with a new auth.token_ttl mutates the running
        AuthService's ttl_seconds so subsequent mints use it."""
        from bed.main import _reload_config_and_apply

        bed = self._bed(token_ttl=900)
        bed.auth_service = MagicMock()
        bed.auth_service.ttl_seconds = 900

        args = self._bed_args(token_ttl=900, config_file="/dev/null")
        new_cfg = {"auth": {"token_ttl": 1234}}
        captured: list = []
        ar_ref = [False]
        rd_ref = [5]
        mr_ref = [10]

        def _capture(msg, level="info"):
            captured.append((level, msg))
            return None

        with patch("bed.main.io.echo", _capture), \
             patch("bed.main.config.load_config", return_value=new_cfg):
            _reload_config_and_apply(args, bed, ar_ref, rd_ref, mr_ref)

        self.assertEqual(bed.auth_service.ttl_seconds, 1234)
        self.assertEqual(args.token_ttl, 1234)
        self.assertTrue(
            any("Live reload: token_ttl=1234" in m for _, m in captured),
            f"missing token_ttl reload log; captured={captured}",
        )

    def test_sighup_applies_autorestart_settings(self) -> None:
        """SIGHUP with new bed.autorestart/restart_delay/max_restarts
        updates the loop locals so the next crash uses the new policy."""
        from bed.main import _reload_config_and_apply

        bed = self._bed()
        bed.auth_service = None
        args = self._bed_args()
        new_cfg = {
            "bed": {
                "autorestart": True,
                "restart_delay": 30,
                "max_restarts": 7,
            }
        }
        ar_ref = [False]
        rd_ref = [5]
        mr_ref = [10]

        with patch("bed.main.config.load_config", return_value=new_cfg):
            _reload_config_and_apply(args, bed, ar_ref, rd_ref, mr_ref)

        self.assertEqual(ar_ref[0], True)
        self.assertEqual(rd_ref[0], 30)
        self.assertEqual(mr_ref[0], 7)

    def test_sighup_warns_on_structural_changes(self) -> None:
        """SIGHUP detects bind/database/persistence/provider/secret
        changes but does NOT apply them; warns 'restart required'."""
        from bed.main import _reload_config_and_apply

        bed = self._bed()
        bed.auth_service = None
        args = self._bed_args(
            host="127.0.0.1",
            port=8765,
            databasename="zoid6",
            databasehost="db.local",
            token_persistence="memory",
            credential_provider="password",
        )
        new_cfg = {
            "bind": {"host": "0.0.0.0", "port": 9001},
            "database": {"name": "zoid7", "host": "db2.local"},
            "auth": {"token_persistence": "db", "credential_provider": "moniker-only"},
        }
        ar_ref = [False]
        rd_ref = [5]
        mr_ref = [10]
        captured: list = []

        def _capture(msg, level="info"):
            captured.append((level, msg))
            return None

        with patch("bed.main.io.echo", _capture), \
             patch("bed.main.config.load_config", return_value=new_cfg):
            _reload_config_and_apply(args, bed, ar_ref, rd_ref, mr_ref)

        # Args are NOT mutated for structural keys.
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)
        self.assertEqual(args.databasename, "zoid6")
        self.assertEqual(args.token_persistence, "memory")
        # But a warning was logged.
        warnings = [m for lvl, m in captured if lvl == "warning"]
        self.assertTrue(
            any("restart required" in m for m in warnings),
            f"missing structural warning; captured={captured}",
        )
        self.assertTrue(
            any("host=" in m for m in warnings),
            f"missing host= in warning; captured={captured}",
        )

    def test_sighup_handles_missing_config_file(self) -> None:
        """SIGHUP with a missing config file logs an error and exits
        the reload path silently."""
        from bed.main import _reload_config_and_apply

        bed = self._bed()
        bed.auth_service = None
        args = self._bed_args(config_file="/nonexistent/path.json")
        ar_ref = [False]
        rd_ref = [5]
        mr_ref = [10]
        captured: list = []

        def _capture(msg, level="info"):
            captured.append((level, msg))
            return None

        with patch("bed.main.io.echo", _capture), \
             patch(
                 "bed.main.config.load_config",
                 side_effect=OSError("no such file"),
             ):
            _reload_config_and_apply(args, bed, ar_ref, rd_ref, mr_ref)

        errors = [m for lvl, m in captured if lvl == "error"]
        self.assertTrue(
            any("Config reload failed" in m for m in errors),
            f"missing reload-failed log; captured={captured}",
        )


class TestSighupHandler(unittest.TestCase):
    """The SIGHUP handler must use the bed_holder so it works on the
    running bed. When no bed is running yet, it logs a warning instead
    of silently no-op'ing (which would have left the old code path with
    a stale config reference)."""

    def test_sighup_warns_when_bed_holder_empty(self) -> None:
        """Reaching sighup_handler before bed_holder[0] is set logs a
        warning and returns without touching anything."""
        # bed/__init__.py shadows ``bed.main`` with the ``main()`` function,
        # so we go through importlib to reach the module.
        import importlib

        bed_main = importlib.import_module("bed.main")

        bed_holder: list = [None]
        captured: list = []

        def _capture(msg, level="info"):
            captured.append((level, msg))
            return None

        with patch.object(bed_main.io, "echo", _capture):
            bed_main._reload_config_and_apply = MagicMock()
            args = argparse.Namespace(config_file="/dev/null")
            current = bed_holder[0]
            if current is None:
                bed_main.io.echo(
                    "SIGHUP received before bed is running; ignoring",
                    level="warning",
                )
            else:
                bed_main._reload_config_and_apply(args, current, [False], [5], [10])

        warnings = [m for lvl, m in captured if lvl == "warning"]
        self.assertTrue(
            any("SIGHUP received before bed is running" in m for m in warnings)
        )
        bed_main._reload_config_and_apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
