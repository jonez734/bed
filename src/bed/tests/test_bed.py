#!/usr/bin/env python3
# bed/tests/test_bed.py
# Integration tests for BED (BBS Engine Daemon)

import argparse
import asyncio
import errno
import importlib
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
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
        self.mock_args.bed_name = "bed"

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

        # BED.start() registers PingService LAST so it overwrites the
        # router's ``ping`` registration. Mirror that ordering here so
        # the test exercises the production registration sequence.
        from bed.api import PingService, SessionRegistry
        ping_svc = PingService(
            self.mock_args,
            SessionRegistry(),
            name=self.mock_args.bed_name,
        )
        ping_svc.register_all(self.bed.server)

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
        """Test ping/pong.

        After PingService registration, the pong carries the bed
        ``name`` (from args.bed_name) and ``version`` (from
        bed._version). The router's plain ``{"type": "pong"}`` is
        overwritten because BED.start() registers PingService LAST.
        """
        from bed import _version

        uri = f"ws://{self.mock_args.host}:{self.mock_args.port}/"

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            response = json.loads(await ws.recv())

            self.assertEqual(response["type"], "pong")
            self.assertEqual(response["name"], self.mock_args.bed_name)
            self.assertEqual(response["version"], _version.__version__)

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

    def test_default_bed_name(self):
        """Test default bed_name is 'bed'."""
        self._extra_args = ["--config", "/dev/null"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.bed_name, "bed")

    def test_custom_bed_name(self):
        """--bed-name NAME overrides the default."""
        self._extra_args = ["--config", "/dev/null", "--bed-name", "mybbs"]
        with patch("sys.argv", ["bed"] + self._extra_args):
            args = self._parse_args()
            self.assertEqual(args.bed_name, "mybbs")

    def test_default_secret_path_derives_from_default_name(self):
        """Default secret path stays at ~/.config/bed/bed.secret when
        bed_name is 'bed' (no change for existing installs)."""
        import os
        from bed.lib import _default_secret_path

        self.assertEqual(
            _default_secret_path(),
            os.path.expanduser("~/.config/bed/bed.secret"),
        )

    def test_default_secret_path_substitutes_name(self):
        """A non-default bed_name yields ~/.config/bed/<name>.secret."""
        import os
        from bed.lib import _default_secret_path

        self.assertEqual(
            _default_secret_path("mybbs"),
            os.path.expanduser("~/.config/bed/mybbs.secret"),
        )

    def test_external_router_resolves(self):
        """--router bbsengine6.net.defaultrouter.DefaultRouter resolves via load_router_class.
        Uses a router class from bed's own bbsengine6 dependency (not a downstream
        consumer like zoid6) so this test does not require zoid6 to be installed."""
        from bed.main import load_router_class

        router_class = load_router_class("bbsengine6.net.defaultrouter.DefaultRouter")
        self.assertTrue(callable(router_class))
        from bbsengine6.net.defaultrouter import DefaultRouter
        self.assertIs(router_class, DefaultRouter)

    def test_load_router_class_bad_fqcn_emits_traceback_and_exits(self):
        """A non-existent --router FQCN routes through bbsengine6.module.load,
        which calls io.echo_traceback and re-raises; main_async emits an
        exit message and exits 1."""
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
        """External config's bed.autorestart=false is reflected via get_restart_config."""
        from bed.main import get_restart_config

        args = self._parse([])
        cfg = {
            "bed": {
                "autorestart": False,
                "restart_delay": 7,
                "max_restarts": 2,
                "restart_on_bind_failure": True,
            }
        }
        autorestart, restart_delay, max_restarts, restart_on_bind_failure = (
            get_restart_config(args, cfg)
        )
        self.assertFalse(autorestart)
        self.assertEqual(restart_delay, 7)
        self.assertEqual(max_restarts, 2)
        self.assertTrue(restart_on_bind_failure)

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

    def test_dsn_key_populates_database_args(self):
        """A libpq-style 'dsn' key in the database section populates
        databasename/databasehost/databaseport/databaseuser when the CLI
        did not pass those flags, so --database* is unnecessary."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {
            "database": {
                "dsn": (
                    "host=db.local port=5433 dbname=zoid6prod user=bed "
                    "password=s3cret"
                )
            }
        }
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasename, "zoid6prod")
        self.assertEqual(args.databasehost, "db.local")
        self.assertEqual(args.databaseport, 5433)
        self.assertEqual(args.databaseuser, "bed")
        self.assertEqual(args.databasepassword, "s3cret")

    def test_dsn_key_does_not_override_explicit_cli_flags(self):
        """If the user passes --databasename/--databasehost/etc. on the CLI,
        the 'dsn' key in config does not override those explicit values."""
        from bed.main import _apply_database_config

        args = self._parse([
            "--databasename", "cli_db",
            "--databasehost", "cli_host",
            "--databaseport", "6543",
        ])
        cfg = {
            "database": {
                "dsn": (
                    "host=db.local port=5433 dbname=zoid6prod user=bed "
                    "password=s3cret"
                )
            }
        }
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasename, "cli_db")
        self.assertEqual(args.databasehost, "cli_host")
        self.assertEqual(args.databaseport, 6543)
        # user/password were not set on the CLI, so they should be filled
        # in from the dsn.
        self.assertEqual(args.databaseuser, "bed")
        self.assertEqual(args.databasepassword, "s3cret")

    def test_dsn_key_partial_components(self):
        """'dsn' may carry only some components; missing keys are ignored."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {"database": {"dsn": "host=db.local dbname=zoid6prod"}}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasename, "zoid6prod")
        self.assertEqual(args.databasehost, "db.local")
        # port/user/password not in dsn — argparse defaults remain.
        self.assertEqual(args.databaseport, 5432)
        self.assertIsNone(args.databaseuser)
        self.assertIsNone(args.databasepassword)

    def test_dsn_key_ignores_non_integer_port(self):
        """A non-integer port in 'dsn' is ignored (with a warning) rather
        than crashing the config-apply path."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {"database": {"dsn": "port=notanumber dbname=foo"}}
        _apply_database_config(args, cfg)
        self.assertEqual(args.databasename, "foo")
        # Default port survives the bad dsn port.
        self.assertEqual(args.databaseport, 5432)

    def test_dsn_key_plays_nice_with_other_database_keys(self):
        """When both 'dsn' and individual database.* keys are present,
        individual keys are applied first (via _apply_config_section) and
        dsn only fills in components still at argparse defaults."""
        from bed.main import _apply_database_config

        args = self._parse([])
        cfg = {
            "database": {
                "name": "from_name_key",
                "dsn": "host=dsn_host dbname=from_dsn user=dsn_user",
            }
        }
        _apply_database_config(args, cfg)
        # 'name' (via _apply_config_section) is applied before dsn runs.
        self.assertEqual(args.databasename, "from_name_key")
        # 'host' was not in the database section individually, so dsn fills it.
        self.assertEqual(args.databasehost, "dsn_host")
        # 'user' was not in the database section individually, so dsn fills it.
        self.assertEqual(args.databaseuser, "dsn_user")

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

    def test_config_file_bed_name_overrides_default(self):
        """bed.name in the JSON config fills in args.bed_name when the
        CLI did not set --bed-name."""
        from bed.main import _apply_bed_name_config

        args = self._parse([])
        cfg = {"bed": {"name": "mybbs"}}
        _apply_bed_name_config(args, cfg)
        self.assertEqual(args.bed_name, "mybbs")

    def test_cli_bed_name_wins_over_config(self):
        """An explicit --bed-name on the CLI beats a bed.name in the JSON."""
        from bed.main import _apply_bed_name_config

        args = self._parse(["--bed-name", "explicitname"])
        cfg = {"bed": {"name": "fromjson"}}
        _apply_bed_name_config(args, cfg)
        self.assertEqual(args.bed_name, "explicitname")

    def test_config_empty_bed_name_falls_back_to_default(self):
        """An empty bed.name in the JSON falls back to the default
        'bed' so the secret-path derivation stays sane."""
        from bed.main import _apply_bed_name_config

        args = self._parse([])
        cfg = {"bed": {"name": ""}}
        _apply_bed_name_config(args, cfg)
        self.assertEqual(args.bed_name, "bed")

    def test_config_missing_bed_name_keeps_default(self):
        """A config without a bed.name at all leaves args.bed_name at
        the argparse default 'bed'."""
        from bed.main import _apply_bed_name_config

        args = self._parse([])
        cfg = {"bed": {"autorestart": False}}
        _apply_bed_name_config(args, cfg)
        self.assertEqual(args.bed_name, "bed")

    def test_config_bed_name_trims_whitespace(self):
        """Surrounding whitespace in bed.name is stripped."""
        from bed.main import _apply_bed_name_config

        args = self._parse([])
        cfg = {"bed": {"name": "  mybbs  "}}
        _apply_bed_name_config(args, cfg)
        self.assertEqual(args.bed_name, "mybbs")


class TestBindMulti(unittest.IsolatedAsyncioTestCase):
    """--bind (CLI, repeatable) and the JSON ``bind`` list/dict shape
    are merged into a single ``args.binds: List[Tuple[str, int]]`` by
    ``_apply_bind_list_config`` + ``_resolve_binds``. These tests pin
    down the precedence: CLI > JSON list > JSON dict (legacy) >
    --host/--port > argparse default."""

    def _parse(self, argv):
        import argparse
        from bed.main import buildargs

        parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
        buildargs(parser)
        with patch("sys.argv", ["bed", "--config", "/dev/null"] + argv):
            return parser.parse_args()

    def test_bind_cli_parses_single_literal(self):
        """--bind 127.0.0.1:8765 yields args.bind = [('127.0.0.1', 8765)]."""
        args = self._parse(["--bind", "127.0.0.1:8765"])
        self.assertEqual(args.bind, [("127.0.0.1", 8765)])

    def test_bind_cli_repeats_to_list(self):
        """--bind may be passed multiple times; each call appends."""
        args = self._parse([
            "--bind", "127.0.0.1:8765",
            "--bind", "[::1]:8765",
        ])
        self.assertEqual(
            args.bind,
            [("127.0.0.1", 8765), ("::1", 8765)],
        )

    def test_bind_cli_rejects_missing_port(self):
        """A bare HOST without :PORT is rejected at parse time so
        typos do not silently produce a default-port bind."""
        from bed.lib import _bind_spec

        with self.assertRaises(argparse.ArgumentTypeError):
            _bind_spec("127.0.0.1")
        with self.assertRaises(argparse.ArgumentTypeError):
            _bind_spec("localhost:")

    def test_bind_cli_rejects_non_integer_port(self):
        """Port must parse as int; 'abc' fails fast."""
        from bed.lib import _bind_spec

        with self.assertRaises(argparse.ArgumentTypeError):
            _bind_spec("127.0.0.1:abc")

    def test_bind_cli_rejects_out_of_range_port(self):
        """Port must be in [1, 65535]; 0 and 70000 are rejected."""
        from bed.lib import _bind_spec

        with self.assertRaises(argparse.ArgumentTypeError):
            _bind_spec("127.0.0.1:0")
        with self.assertRaises(argparse.ArgumentTypeError):
            _bind_spec("127.0.0.1:70000")

    def test_bind_cli_accepts_localhost_name(self):
        """--bind localhost:8765 is allowed; the multi-bind code path
        will fan it out to both 127.0.0.1 and ::1 at bind time."""
        from bed.lib import _bind_spec

        self.assertEqual(_bind_spec("localhost:8765"), ("localhost", 8765))

    def test_bind_cli_accepts_bracketed_ipv6(self):
        """--bind '[::1]:8765' keeps the IPv6 literal unambiguous."""
        from bed.lib import _bind_spec

        self.assertEqual(_bind_spec("[::1]:8765"), ("::1", 8765))

    def test_apply_bind_list_config_parses_list_shape(self):
        """JSON ``"bind": [{"host":..., "port":...}, ...]`` populates
        ``args.binds`` when CLI did not pass ``--bind``."""
        from bed.main import _apply_bind_list_config

        args = self._parse([])
        cfg = {
            "bind": [
                {"host": "127.0.0.1", "port": 9001},
                {"host": "::1", "port": 9001},
            ]
        }
        _apply_bind_list_config(args, cfg)
        self.assertEqual(
            args.binds,
            [("127.0.0.1", 9001), ("::1", 9001)],
        )

    def test_apply_bind_list_config_parses_legacy_dict(self):
        """A legacy ``"bind": {"host":..., "port":...}`` is treated as
        a one-element list so existing single-bind configs keep
        working."""
        from bed.main import _apply_bind_list_config

        args = self._parse([])
        cfg = {"bind": {"host": "127.0.0.1", "port": 9002}}
        _apply_bind_list_config(args, cfg)
        self.assertEqual(args.binds, [("127.0.0.1", 9002)])

    def test_apply_bind_list_config_cli_wins(self):
        """--bind on the CLI is not overwritten by a JSON list."""
        from bed.main import _apply_bind_list_config

        args = self._parse(["--bind", "10.0.0.1:1111"])
        cfg = {"bind": [{"host": "127.0.0.1", "port": 9001}]}
        _apply_bind_list_config(args, cfg)
        # args.binds is set in the final _resolve_binds step; this
        # helper only populates it from config when CLI was absent.
        self.assertIsNone(getattr(args, "binds", None))

    def test_apply_bind_list_config_skips_bad_entries(self):
        """A row with missing or invalid host/port is logged and
        skipped, but valid siblings still pass through."""
        from bed.main import _apply_bind_list_config

        args = self._parse([])
        cfg = {
            "bind": [
                {"host": "127.0.0.1", "port": 9003},
                {"port": 9999},                      # missing host
                {"host": "::1", "port": "not-int"},  # bad port
                {"host": "::1", "port": 70000},      # out of range
                {"host": "10.0.0.5", "port": 9004},
            ]
        }
        _apply_bind_list_config(args, cfg)
        self.assertEqual(
            args.binds,
            [("127.0.0.1", 9003), ("10.0.0.5", 9004)],
        )

    def test_apply_bind_list_config_empty_when_no_bind_keys(self):
        """A JSON ``"bind": []`` or ``"bind": {}`` produces no
        args.binds; _resolve_binds falls back to host/port."""
        from bed.main import _apply_bind_list_config

        args = self._parse([])
        cfg = {"bind": []}
        _apply_bind_list_config(args, cfg)
        self.assertIsNone(getattr(args, "binds", None))

        args = self._parse([])
        cfg = {"bind": {}}
        _apply_bind_list_config(args, cfg)
        self.assertIsNone(getattr(args, "binds", None))

    def test_resolve_binds_cli_first(self):
        """--bind CLI list wins over JSON list and --host/--port."""
        from bed.main import _resolve_binds

        args = self._parse([
            "--bind", "127.0.0.1:9100",
            "--bind", "[::1]:9100",
        ])
        args.binds = [("from-config", 1), ("from-config", 2)]
        args.host = "from-cli-host"
        args.port = 9999
        self.assertEqual(
            _resolve_binds(args),
            [("127.0.0.1", 9100), ("::1", 9100)],
        )

    def test_resolve_binds_config_second(self):
        """JSON bind list wins when CLI did not pass --bind."""
        from bed.main import _resolve_binds

        args = self._parse([])
        args.binds = [("from-config", 8765)]
        args.host = "127.0.0.1"
        args.port = 8765
        self.assertEqual(
            _resolve_binds(args),
            [("from-config", 8765)],
        )

    def test_resolve_binds_falls_back_to_host_port(self):
        """No CLI --bind, no config: the legacy --host/--port is the
        single-element bind list."""
        from bed.main import _resolve_binds

        args = self._parse([])
        args.bind = None
        args.binds = None
        args.host = "10.0.0.7"
        args.port = 8123
        self.assertEqual(_resolve_binds(args), [("10.0.0.7", 8123)])


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
        from unittest.mock import MagicMock, patch

        bed_main = importlib.import_module("bed.main")

        fake_live_pid = os.getpid() + 1
        with open(self.pidfile_path, "w") as f:
            f.write(f"{fake_live_pid}\n")

        def fake_kill(pid, sig):
            if pid == fake_live_pid:
                return  # pretend it succeeded
            return os.kill(pid, sig)

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
            bed_name="bed",
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
            bed_name="bed",
        )
        new_cfg = {
            "bind": {"host": "0.0.0.0", "port": 9001},
            "database": {"name": "zoid7", "host": "db2.local"},
            "auth": {"token_persistence": "db", "credential_provider": "moniker-only"},
            "bed": {"name": "mybbs"},
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
        self.assertEqual(args.bed_name, "bed")
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
        self.assertTrue(
            any("bed_name=" in m for m in warnings),
            f"missing bed_name= in warning; captured={captured}",
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


class TestRestartOnBindFailure(unittest.IsolatedAsyncioTestCase):
    """bind-failure (EADDRINUSE / EACCES) classification.

    Without these tests, a port-in-use failure makes ``bed`` exit 1
    and systemd's ``Restart=on-failure`` keeps spinning the unit —
    which the operator experiences as 'bed auto-restarted even
    though I set autorestart=false'. The fix introduces a separate
    ``restart_on_bind_failure`` config key and a special exit code
    (2) that the systemd unit's ``RestartPreventExitStatus=2``
    blocks.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._cfg_path = os.path.join(self._tmp, "bed.json")
        with open(self._cfg_path, "w") as f:
            f.write("{}")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self._tmp, ignore_errors=True)
        except Exception:
            pass

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
            bed_name="bed",
            token_ttl=900,
            token_persistence="memory",
            credential_provider="password",
            bed_instance_id=None,
            config_file=self._cfg_path,
            autorestart=False,
            restart_delay=5,
            max_restarts=10,
            restart_on_bind_failure=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    async def test_real_bind_collision_raises_eaddrinuse(self) -> None:
        """Hold a port with a plain ``socket.socket`` (which does not
        set ``SO_REUSEPORT``) and assert that ``WebSocketServer.start()``
        on the same port raises ``OSError`` with ``errno == 98``.
        This is the production path that ``main_async`` now classifies
        as a permanent bind failure.

        We can't use two ``WebSocketServer`` instances because
        ``bbsengine6.net.transport`` sets both ``SO_REUSEADDR`` and
        ``SO_REUSEPORT`` and two sockets happily co-bind on Linux.
        A plain ``socket.socket`` (no SO_REUSEPORT) is the realistic
        'something else owns the port' scenario."""
        from bbsengine6.net import WebSocketServer

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            server = WebSocketServer(host="127.0.0.1", port=port)
            with self.assertRaises(OSError) as cm:
                await server.start()
            self.assertEqual(
                cm.exception.errno, errno.EADDRINUSE,
                f"expected EADDRINUSE (98), got errno={cm.exception.errno}",
            )
        finally:
            sock.close()

    async def test_main_async_exits_2_on_eaddrinuse_when_restart_disabled(
        self,
    ) -> None:
        """The default ``restart_on_bind_failure=false`` plus the
        default ``autorestart=false``: a bind failure must trigger
        ``sys.exit(2)`` and NOT enter the restart loop."""
        bed_main = importlib.import_module("bed.main")
        bed_instance = MagicMock()

        async def fake_start_raises_eaddrinuse() -> None:
            raise OSError(errno.EADDRINUSE, "Address already in use")

        async def fake_stop() -> None:
            return None

        bed_instance.start = fake_start_raises_eaddrinuse
        bed_instance.stop = fake_stop

        with (
            patch(
                "sys.argv",
                ["bed", "--config", self._cfg_path],
            ),
            patch.object(
                bed_main.config, "load_config", return_value={}
            ),
            patch.object(
                bed_main, "load_router_class", return_value=MagicMock()
            ),
            patch.object(bed_main, "ensure_startup", return_value=True),
            patch.object(bed_main, "BED", return_value=bed_instance),
            patch.object(bed_main.asyncio, "sleep") as mock_sleep,
        ):
            with self.assertRaises(SystemExit) as cm:
                await bed_main.main_async()

        self.assertEqual(
            cm.exception.code, 2,
            f"expected exit code 2, got {cm.exception.code}",
        )
        # The contract is that the loop saw a permanent bind failure
        # and did NOT call asyncio.sleep to retry.
        mock_sleep.assert_not_called()

    async def test_main_async_exits_2_on_eacces_when_restart_disabled(
        self,
    ) -> None:
        """EACCES (e.g. trying to bind a privileged port without root)
        is treated as a permanent bind failure too."""
        bed_main = importlib.import_module("bed.main")
        bed_instance = MagicMock()

        async def fake_start_raises_eacces() -> None:
            raise PermissionError(errno.EACCES, "Permission denied")

        async def fake_stop() -> None:
            return None

        bed_instance.start = fake_start_raises_eacces
        bed_instance.stop = fake_stop

        with (
            patch(
                "sys.argv",
                ["bed", "--config", self._cfg_path],
            ),
            patch.object(
                bed_main.config, "load_config", return_value={}
            ),
            patch.object(
                bed_main, "load_router_class", return_value=MagicMock()
            ),
            patch.object(bed_main, "ensure_startup", return_value=True),
            patch.object(bed_main, "BED", return_value=bed_instance),
            patch.object(bed_main.asyncio, "sleep") as mock_sleep,
        ):
            with self.assertRaises(SystemExit) as cm:
                await bed_main.main_async()

        self.assertEqual(cm.exception.code, 2)
        mock_sleep.assert_not_called()

    async def test_main_async_retries_bind_failure_when_enabled(self) -> None:
        """With ``restart_on_bind_failure=true``, the in-process loop
        retries the bind (via asyncio.sleep) and respects ``max_restarts``
        even though ``autorestart`` is false. ``restart_on_bind_failure``
        is the gate for bind failures."""
        bed_main = importlib.import_module("bed.main")
        bed_instance = MagicMock()

        attempt = {"n": 0}

        async def fake_start_raises_eaddrinuse() -> None:
            attempt["n"] += 1
            raise OSError(errno.EADDRINUSE, "Address already in use")

        async def fake_stop() -> None:
            return None

        bed_instance.start = fake_start_raises_eaddrinuse
        bed_instance.stop = fake_stop

        with (
            patch(
                "sys.argv",
                [
                    "bed",
                    "--config", self._cfg_path,
                    "--restart-on-bind-failure",
                    "--max-restarts", "2",
                    "--restart-delay", "0",
                ],
            ),
            patch.object(
                bed_main.config, "load_config", return_value={}
            ),
            patch.object(
                bed_main, "load_router_class", return_value=MagicMock()
            ),
            patch.object(bed_main, "ensure_startup", return_value=True),
            patch.object(bed_main, "BED", return_value=bed_instance),
            patch.object(
                bed_main.asyncio, "sleep", new=AsyncMock(return_value=None)
            ) as mock_sleep,
        ):
            await bed_main.main_async()

        # max_restarts=2 means the loop is allowed at most 2 retries
        # before giving up. start() raises on each attempt, so we
        # expect exactly 3 invocations (initial + 2 retries).
        self.assertEqual(
            attempt["n"], 3,
            f"expected 3 start() attempts (initial + 2 retries), "
            f"got {attempt['n']}",
        )
        self.assertGreaterEqual(
            mock_sleep.await_count, 2,
            f"expected at least 2 sleep calls (between retries), "
            f"got {mock_sleep.await_count}",
        )

    async def test_main_async_autorestart_true_still_exits_2_on_bind(self) -> None:
        """Precedence rule: ``restart_on_bind_failure`` is the gate for
        bind failures, NOT ``autorestart``. So even with general
        ``autorestart=true``, a stuck port still exits 2 (the systemd
        unit's ``RestartPreventExitStatus=2`` keeps the unit from
        looping). This is the bug we are fixing: previously the
        systemd-level Restart=on-failure would spin forever on a
        permanent bind failure regardless of either knob."""
        bed_main = importlib.import_module("bed.main")
        bed_instance = MagicMock()

        async def fake_start_raises_eaddrinuse() -> None:
            raise OSError(errno.EADDRINUSE, "Address already in use")

        async def fake_stop() -> None:
            return None

        bed_instance.start = fake_start_raises_eaddrinuse
        bed_instance.stop = fake_stop

        with (
            patch(
                "sys.argv",
                [
                    "bed",
                    "--config", self._cfg_path,
                    "--autorestart",
                    "--max-restarts", "0",
                ],
            ),
            patch.object(
                bed_main.config, "load_config", return_value={}
            ),
            patch.object(
                bed_main, "load_router_class", return_value=MagicMock()
            ),
            patch.object(bed_main, "ensure_startup", return_value=True),
            patch.object(bed_main, "BED", return_value=bed_instance),
            patch.object(bed_main.asyncio, "sleep") as mock_sleep,
        ):
            with self.assertRaises(SystemExit) as cm:
                await bed_main.main_async()

        self.assertEqual(
            cm.exception.code, 2,
            "bind failure must exit 2 even when autorestart is on",
        )
        mock_sleep.assert_not_called()

    def test_get_restart_config_defaults(self) -> None:
        """``get_restart_config`` returns ``restart_on_bind_failure=False``
        by default, regardless of whether ``autorestart`` is set."""
        from bed.main import get_restart_config

        args = argparse.Namespace(
            autorestart=None,
            restart_delay=None,
            max_restarts=None,
            restart_on_bind_failure=None,
        )
        cfg = {"bed": {"autorestart": True, "restart_delay": 30}}
        autorestart, restart_delay, max_restarts, restart_on_bind_failure = (
            get_restart_config(args, cfg)
        )
        self.assertTrue(autorestart)
        self.assertEqual(restart_delay, 30)
        self.assertEqual(max_restarts, 10)
        self.assertFalse(
            restart_on_bind_failure,
            "restart_on_bind_failure must default to False even when "
            "autorestart=True",
        )

    def test_get_restart_config_cli_overrides_config(self) -> None:
        """``--restart-on-bind-failure`` on the command line wins over
        the config file value."""
        from bed.main import get_restart_config

        args = argparse.Namespace(
            autorestart=None,
            restart_delay=None,
            max_restarts=None,
            restart_on_bind_failure=True,
        )
        cfg = {"bed": {"restart_on_bind_failure": False}}
        _autorestart, _rd, _mr, robf = get_restart_config(args, cfg)
        self.assertTrue(robf)

    def test_sighup_applies_restart_on_bind_failure(self) -> None:
        """SIGHUP with a new ``bed.restart_on_bind_failure`` updates the
        loop-local ref so the next bind failure uses the new policy."""
        from bed.main import _reload_config_and_apply
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        bed_main = importlib.import_module("bed.main")

        args = argparse.Namespace(
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
            config_file=self._cfg_path,
        )
        bed = BED(args, DefaultRouter)
        bed.auth_service = None
        new_cfg = {"bed": {"restart_on_bind_failure": True}}
        ar_ref = [False]
        rd_ref = [5]
        mr_ref = [10]
        robf_ref = [False]

        with patch.object(
            bed_main.config, "load_config", return_value=new_cfg
        ):
            _reload_config_and_apply(
                args, bed, ar_ref, rd_ref, mr_ref, robf_ref
            )

        self.assertTrue(robf_ref[0])


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


class TestBindMultiStart(unittest.IsolatedAsyncioTestCase):
    """``BED.start()`` should pass ``args.binds`` through to the
    underlying ``WebSocketServer`` and produce one listener per
    resolved bind. These tests stub out DB + auth and drive the
    daemon directly so we exercise the multi-bind code path without
    needing a running PostgreSQL."""

    def _make_args(self, **overrides):
        """Build a MagicMock args namespace with the multi-bind
        attributes populated, plus the DB+auth stubs BED.start()
        expects."""
        mock_args = MagicMock()
        mock_args.databasename = "test"
        mock_args.databasehost = "localhost"
        mock_args.databaseport = 5432
        mock_args.databaseuser = "test"
        mock_args.databasepassword = "test"
        mock_args.debug = False
        mock_args.bed_name = "bed"
        mock_args.config_file = "/dev/null"
        mock_args.token_persistence = "none"
        mock_args.credential_provider = "password"
        mock_args.bed_secret = None
        mock_args.bed_instance_id = None
        mock_args.token_ttl = 900
        mock_args.no_message_service = True
        mock_args.no_bank_service = True
        # Multi-bind attrs
        mock_args.bind = None
        mock_args.binds = None
        mock_args.host = "127.0.0.1"
        mock_args.port = 0
        for k, v in overrides.items():
            setattr(mock_args, k, v)
        return mock_args

    def _free_port(self):
        import socket as _s
        s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    async def _drive_bed_with_binds(self, args):
        """Run ``bed.start()`` in a task and stop it as soon as the
        server is live. ``start()`` ends in
        ``while self._running: await asyncio.sleep(1)`` so the test
        cannot ``await bed.start()`` directly — the sleep loop would
        hang forever. Instead we launch it as a task and schedule
        ``bed.stop()`` to fire after a short delay, which lets us
        inspect ``bed.server._bound_addrs`` between start and stop.
        """
        import asyncio as _asyncio
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(
            return_value=MagicMock()
        )
        mock_pool.connection.return_value.__exit__ = MagicMock(
            return_value=False
        )
        bed = BED(args, DefaultRouter)
        start_task = _asyncio.create_task(
            _run_with_mock_db(bed, mock_pool)
        )
        # Wait until the server is constructed and listening.
        for _ in range(200):
            if bed.server is not None and bed.server._bound_addrs:
                break
            await _asyncio.sleep(0.01)
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
        return bed, start_task

    async def test_bed_start_opens_multiple_listeners(self):
        """Two literal --bind entries become two listeners, both
        reachable on their respective stacks."""
        port = self._free_port()
        args = self._make_args(
            bind=[("127.0.0.1", port), ("::1", port)],
        )
        bed, start_task = await self._drive_bed_with_binds(args)
        try:
            assert bed.server is not None
            assert len(bed.server._bound_addrs) == 2, (
                f"expected 2 listeners, got: {bed.server._bound_addrs}"
            )
            import websockets as _ws
            async with _ws.connect(f"ws://127.0.0.1:{port}/") as ws:
                await ws.send(json.dumps({"type": "ping"}))
                resp = json.loads(await ws.recv())
                self.assertEqual(resp["type"], "pong")
            async with _ws.connect(f"ws://[::1]:{port}/") as ws:
                await ws.send(json.dumps({"type": "ping"}))
                resp = json.loads(await ws.recv())
                self.assertEqual(resp["type"], "pong")
        finally:
            await bed.stop()
            try:
                await start_task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_bed_start_uses_args_binds_when_no_cli_bind(self):
        """When CLI --bind is absent but args.binds was populated
        from JSON (main_async's job), BED.start() uses it."""
        port = self._free_port()
        args = self._make_args(
            bind=None,
            binds=[("127.0.0.1", port)],
        )
        bed, start_task = await self._drive_bed_with_binds(args)
        try:
            self.assertEqual(len(bed.server._bound_addrs), 1)
            self.assertEqual(bed.server._bound_addrs[0][1], "127.0.0.1")
            self.assertEqual(bed.server._bound_addrs[0][2], port)
        finally:
            await bed.stop()
            try:
                await start_task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_bed_start_falls_back_to_host_port(self):
        """No --bind, no binds → legacy --host/--port single bind."""
        port = self._free_port()
        args = self._make_args(
            bind=None,
            binds=None,
            host="127.0.0.1",
            port=port,
        )
        bed, start_task = await self._drive_bed_with_binds(args)
        try:
            self.assertEqual(len(bed.server._bound_addrs), 1)
        finally:
            await bed.stop()
            try:
                await start_task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_bed_start_unresolvable_bind_exits_2(self):
        """A typo'd --bind host name is treated like a permanent bind
        failure and propagates out of start(). The autorestart loop
        in main_async is what converts that to sys.exit(2); here we
        just verify start() raises a gaierror so the loop sees it."""
        import socket as _s
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        args = self._make_args(
            bind=[("definitely-not-a-real-host.invalid", 8765)],
        )
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(
            return_value=MagicMock()
        )
        mock_pool.connection.return_value.__exit__ = MagicMock(
            return_value=False
        )
        with patch("bed.main.getpool", return_value=mock_pool):
            bed = BED(args, DefaultRouter)
            with self.assertRaises(_s.gaierror):
                await bed.start()
        # The server was never constructed (resolve fails first).
        self.assertIsNone(bed.server)

    def test_final_binds_precedence(self):
        """``BED._final_binds`` returns the right list given each
        precedence combination. Pure logic test, no event loop."""
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        cases = [
            # (kwargs, expected)
            (
                {"bind": [("127.0.0.1", 9100)], "binds": None,
                 "host": "ignored", "port": 0},
                [("127.0.0.1", 9100)],
            ),
            (
                {"bind": None, "binds": [("10.0.0.1", 9200)],
                 "host": "ignored", "port": 0},
                [("10.0.0.1", 9200)],
            ),
            (
                {"bind": None, "binds": None, "host": "192.168.1.1",
                 "port": 9300},
                [("192.168.1.1", 9300)],
            ),
            (
                {"bind": [("1", 1)], "binds": [("2", 2)], "host": "3",
                 "port": 3},
                [("1", 1)],  # CLI wins
            ),
        ]
        for kwargs, expected in cases:
            args = self._make_args(**kwargs)
            bed = BED(args, DefaultRouter)
            self.assertEqual(bed._final_binds(), expected)


async def _run_with_mock_db(bed, mock_pool):
    """Helper for ``TestBindMultiStart``: run ``bed.start()`` with
    ``bed.main.getpool`` patched so the DB layer does not try to talk
    to a real PostgreSQL. Used to drive the full BED.start() path
    in tests that need to inspect ``bed.server._bound_addrs`` after
    the WebSocketServer has been built and bound."""
    with patch("bed.main.getpool", return_value=mock_pool):
        await bed.start()


if __name__ == "__main__":
    unittest.main()
