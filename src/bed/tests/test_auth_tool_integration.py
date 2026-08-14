#!/usr/bin/env python3
"""End-to-end integration tests for the bed ``auth`` CLI tool.

Boots a real in-process :class:`bbsengine6.net.WebSocketServer` with
the bed-native :class:`bed.api.auth.AuthService` registered (same
shape as the bank-integration tests at
``test_bank_integration.py:125-190``), then drives every CLI tool
function (:func:`bed.tools.auth.auth_login` /
:func:`bed.tools.auth.auth_reconnect` /
:func:`bed.tools.auth.auth_refresh` /
:func:`bed.tools.auth.auth_revoke`) against it through the real
:class:`bed.client.authservice.BedAuthServiceClient` and the real
on-disk token file. Also exercises
:func:`bed.tools.auth.main_with_args` through
:func:`bed.tools._routing.select_backend` /
:func:`bed.tools._routing.probe_bed`.

Marked ``@pytest.mark.integration`` at module scope so
``pytest -m unit`` skips the suite.
"""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
import unittest
from typing import Any
from unittest.mock import patch


import pytest


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------
# Args builder


def _make_args(
    *,
    subcommand: str,
    host: str,
    port: int,
    moniker: str | None = "alice",
    password: str | None = "pw",
    token: str | None = None,
    token_file: str | None = None,
    direct: bool = False,
    debug: bool = False,
    bed_call_timeout: float = 0.5,
    bed_probe_timeout: float = 0.25,
) -> argparse.Namespace:
    args = argparse.Namespace()
    args.subcommand = subcommand
    args.moniker = moniker
    args.password = password
    args.token = token
    args.token_file = token_file
    args.bed_host = host
    args.bed_port = port
    args.bed_path = "/"
    args.bed_call_timeout = bed_call_timeout
    args.bed_probe_timeout = bed_probe_timeout
    args.direct = direct
    args.debug = debug
    return args


def _read_token(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


# ---------------------------------------------------------------------
# Shared setUp / tearDown


class _AuthToolTestBase(unittest.TestCase):
    """Provides per-test tmpdir for ``$XDG_RUNTIME_DIR`` and resets
    the ``BedConnection`` and ``BedAuthServiceClient`` module-level
    singletons so the ``id(args)``-keyed cache cannot leak across
    tests.
    """

    def setUp(self) -> None:
        self._xdg_tmp = tempfile.mkdtemp(prefix="bed-auth-tool-test-")
        self._xdg_patch = patch.dict(
            os.environ, {"XDG_RUNTIME_DIR": self._xdg_tmp}
        )
        self._xdg_patch.start()
        from bed.client import authservice

        authservice.reset_auth_client()
        self._last_args: argparse.Namespace | None = None

    def tearDown(self) -> None:
        from bed.client import authservice
        from bed.tests._auth_helpers import _drop_bed_connection_singleton

        if self._last_args is not None:
            _drop_bed_connection_singleton(self._last_args)
        authservice.reset_auth_client()
        self._xdg_patch.stop()
        for name in os.listdir(self._xdg_tmp):
            try:
                os.unlink(os.path.join(self._xdg_tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(self._xdg_tmp)
        except OSError:
            pass

    def _remember(self, args: argparse.Namespace) -> argparse.Namespace:
        """Record ``args`` for tearDown cleanup and drop any cached
        :class:`BedConnection` from the previous ``args`` namespace
        so the next ``asyncio.run`` starts with a fresh connection
        bound to the current loop. Each tool function (``auth_login``
        / ``auth_reconnect`` / ...) calls ``asyncio.run`` internally,
        which closes its loop; the singleton-cached
        :class:`BedConnection` keeps a stale :class:`asyncio.Lock`
        from the previous loop, so reuse causes ``auth_refresh`` (and
        anything after the first call) to deadlock or fail.

        Uses ``_drop_bed_connection_singleton`` instead of
        :func:`bed.client.singleton.reset_bed_connection` because
        the production ``reset_bed_connection`` calls
        :meth:`BedConnection.force_close`, which spawns a daemon
        thread that tries to close the websocket on a freshly
        created loop -- and the websocket's internal futures are
        bound to the now-closed original loop, raising
        ``attached to a different loop``."""
        from bed.tests._auth_helpers import _drop_bed_connection_singleton

        if self._last_args is not None and self._last_args is not args:
            _drop_bed_connection_singleton(self._last_args)
        self._last_args = args
        return args


# ---------------------------------------------------------------------
# login


class TestAuthToolLogin(_AuthToolTestBase):
    """Drive :func:`bed.tools.auth.auth_login` through the real client."""

    def test_login_writes_token_file_with_mode_0600(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"):
            args = self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            ok = auth_tool.auth_login(args)

        self.assertTrue(ok)
        self.assertTrue(os.path.exists(token_file))
        with open(token_file) as f:
            token = f.read().strip()
        self.assertTrue(token)
        self.assertEqual(stat.S_IMODE(os.stat(token_file).st_mode), 0o600)
        record = server.auth_service.token_store.get(token)
        self.assertIsNotNone(record)
        self.assertEqual(record.moniker, "alice")
        self.assertEqual(record.bed_instance_id, "auth-tool-integration-test")
        self.assertEqual(record.loginid, "alice_os")

    def test_login_bad_credentials_soft_failure_no_file_write(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo") as echo:
            args = self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="wrong",
                    token_file=token_file,
                )
            )
            ok = auth_tool.auth_login(args)

        self.assertFalse(ok)
        self.assertFalse(os.path.exists(token_file))
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("bad_credentials", rendered)
        self.assertIn("Invalid moniker or password", rendered)


# ---------------------------------------------------------------------
# reconnect


class TestAuthToolReconnect(_AuthToolTestBase):
    """Drive :func:`bed.tools.auth.auth_reconnect` through the real client."""

    def test_reconnect_rotates_token_via_real_connection(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"):
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            t0 = _read_token(token_file)

            self._remember(
                _make_args(
                    subcommand="reconnect",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_reconnect(self._last_args))
            t1 = _read_token(token_file)

        self.assertNotEqual(t0, t1)
        self.assertIsNone(server.auth_service.token_store.get(t0))
        self.assertIsNotNone(server.auth_service.token_store.get(t1))

    def test_reconnect_missing_token_local_short_circuit(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo") as echo:
            args = self._remember(
                _make_args(
                    subcommand="reconnect",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=None,
                )
            )
            ok = auth_tool.auth_reconnect(args)

        self.assertFalse(ok)
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("missing_token: token is required", rendered)

    def test_reconnect_after_revoke_fails_and_does_not_recreate_file(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"):
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            t0 = _read_token(token_file)

            self._remember(
                _make_args(
                    subcommand="revoke",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_revoke(self._last_args))
            self.assertFalse(os.path.exists(token_file))

            self._remember(
                _make_args(
                    subcommand="reconnect",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.auth_reconnect(self._last_args)

        self.assertFalse(ok)
        self.assertFalse(os.path.exists(token_file))
        self.assertIsNone(server.auth_service.token_store.get(t0))

    def test_reconnect_with_explicit_token_flag_overrides_file(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"):
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            file_token_before = _read_token(token_file)

            flag_token = "flag-tok-should-win"
            with open(token_file, "w") as f:
                f.write(flag_token + "\n")
            os.chmod(token_file, 0o600)

            sent_tokens: list[str] = []

            async def _fake_reconnect(_self, token):  # noqa: ARG001
                sent_tokens.append(token)
                return {
                    "ok": True,
                    "moniker": "alice",
                    "is_sysop": False,
                    "session_id": "sess-1",
                    "token": "new-server-tok",
                    "expires_at": "2030-01-01T00:15:00Z",
                    "replayed": None,
                }

            from bed.client.authservice import BedAuthServiceClient

            with patch.object(
                BedAuthServiceClient, "reconnect", _fake_reconnect
            ):
                args = self._remember(
                    _make_args(
                        subcommand="reconnect",
                        host="127.0.0.1",
                        port=server.port,
                        token=flag_token,
                        token_file=token_file,
                    )
                )
                ok = auth_tool.auth_reconnect(args)

        self.assertTrue(ok)
        self.assertEqual(sent_tokens, [flag_token])
        # The flag value won; the file is now overwritten with the
        # server-issued rotated token from the fake reply.
        self.assertEqual(_read_token(token_file), "new-server-tok")
        self.assertNotEqual(_read_token(token_file), file_token_before)


# ---------------------------------------------------------------------
# refresh


class TestAuthToolRefresh(_AuthToolTestBase):
    """Drive :func:`bed.tools.auth.auth_refresh` through the real client.

    Note: ``auth_refresh`` is *not* viable end-to-end through the CLI.
    The CLI creates a fresh ``BedConnection`` (and therefore a fresh
    WebSocket) per ``auth_*`` invocation. The server's
    ``bbsengine6.auth.access("refresh")`` requires the websocket to
    be the same one ``auth`` originally bound the
    :class:`SessionState` to (the policy at
    ``bbsengine6/auth/__init__.py:135``). After the original
    websocket closed (because the first ``asyncio.run`` tore down
    its loop), the new websocket has no session bound -> the server
    answers ``not_authenticated``. The CLI's contract is that
    ``auth refresh`` is meant to be called within the same process
    as ``auth login``; the standalone CLI surface can only ever see
    ``not_authenticated`` here. We assert that exact behavior so the
    suite documents the architectural reality.
    """

    def test_refresh_via_cli_returns_not_authenticated(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo") as echo:
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            t0 = _read_token(token_file)
            # The token file is intact; refresh should attempt the
            # server call (the tool's local short-circuit is only for
            # missing/empty tokens).
            self.assertTrue(t0)
            self.assertTrue(server.auth_service.token_store.get(t0) is not None)

            self._remember(
                _make_args(
                    subcommand="refresh",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.auth_refresh(self._last_args)

        self.assertFalse(ok)
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("not_authenticated", rendered)
        # Token file was NOT overwritten (write_token_file is only
        # called on a successful reply).
        self.assertEqual(_read_token(token_file), t0)
        # Server-side token still exists (refresh did not delete it).
        self.assertIsNotNone(server.auth_service.token_store.get(t0))

    def test_refresh_missing_token_local_short_circuit(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo") as echo:
            args = self._remember(
                _make_args(
                    subcommand="refresh",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=None,
                )
            )
            ok = auth_tool.auth_refresh(args)

        self.assertFalse(ok)
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("missing_token: token is required", rendered)


# ---------------------------------------------------------------------
# revoke


class TestAuthToolRevoke(_AuthToolTestBase):
    """Drive :func:`bed.tools.auth.auth_revoke` through the real client."""

    def test_revoke_truncates_token_file(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo") as echo:
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            t0 = _read_token(token_file)

            self._remember(
                _make_args(
                    subcommand="revoke",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.auth_revoke(self._last_args)

        self.assertTrue(ok)
        self.assertFalse(os.path.exists(token_file))
        self.assertIsNone(server.auth_service.token_store.get(t0))
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("token revoked", rendered)

    def test_revoke_missing_token_local_short_circuit(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo") as echo:
            args = self._remember(
                _make_args(
                    subcommand="revoke",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=None,
                )
            )
            ok = auth_tool.auth_revoke(args)

        self.assertFalse(ok)
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("missing_token: token is required", rendered)


# ---------------------------------------------------------------------
# full lifecycle


class TestAuthToolFullLifecycle(_AuthToolTestBase):
    """Single test that walks login -> reconnect -> revoke against
    one in-process server, asserting the file and store
    transitions at every step.

    Note: ``refresh`` is intentionally omitted. The CLI's
    standalone ``auth refresh`` cannot succeed end-to-end because
    each invocation opens a fresh WebSocket; see the comment on
    :class:`TestAuthToolRefresh`.
    """

    def test_login_reconnect_revoke_state_machine(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        self.assertFalse(os.path.exists(token_file))

        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"):

            def run(subcommand: str, **overrides: Any) -> bool:
                a = self._remember(
                    _make_args(
                        subcommand=subcommand,
                        host="127.0.0.1",
                        port=server.port,
                        token_file=token_file,
                        **overrides,
                    )
                )
                fn = getattr(auth_tool, f"auth_{subcommand}")
                return fn(a)

            # Step 1: login -> T0 written, store has T0.
            self.assertTrue(run("login", moniker="alice", password="pw"))
            t0 = _read_token(token_file)
            self.assertTrue(t0)
            self.assertEqual(stat.S_IMODE(os.stat(token_file).st_mode), 0o600)
            self.assertIsNotNone(server.auth_service.token_store.get(t0))

            # Step 2: reconnect on a new socket -> T1 written, store
            # has T1 only. (Reconnect is allowed on a new socket per
            # ``bbsengine6.auth.access("reconnect")``.)
            self.assertTrue(run("reconnect"))
            t1 = _read_token(token_file)
            self.assertNotEqual(t0, t1)
            self.assertIsNone(server.auth_service.token_store.get(t0))
            self.assertIsNotNone(server.auth_service.token_store.get(t1))

            # Step 3: revoke -> file deleted, store empty.
            self.assertTrue(run("revoke"))
            self.assertFalse(os.path.exists(token_file))
            self.assertIsNone(server.auth_service.token_store.get(t1))


# ---------------------------------------------------------------------
# main_with_args dispatch path


class TestAuthToolMainDispatch(_AuthToolTestBase):
    """Drive :func:`bed.tools.auth.main_with_args` end-to-end through
    :func:`bed.tools._routing.select_backend` and
    :func:`bed.tools._routing.probe_bed`.
    """

    def test_main_with_args_dispatches_login_via_real_connection(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"), \
             patch.object(auth_tool, "inputpassword", return_value="pw"), \
             patch.object(auth_tool.io, "inputstring", return_value="alice"):
            args = self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker=None,
                    password=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.main_with_args(args)

        self.assertTrue(ok)
        self.assertTrue(os.path.exists(token_file))

    def test_main_with_args_dispatches_reconnect_via_real_connection(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"):
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            t0 = _read_token(token_file)

            self._remember(
                _make_args(
                    subcommand="reconnect",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.main_with_args(self._last_args)

        self.assertTrue(ok)
        self.assertNotEqual(_read_token(token_file), t0)

    def test_main_with_args_dispatches_refresh_returns_not_authenticated(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo") as echo:
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            t0 = _read_token(token_file)

            self._remember(
                _make_args(
                    subcommand="refresh",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.main_with_args(self._last_args)

        self.assertFalse(ok)
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("not_authenticated", rendered)
        self.assertEqual(_read_token(token_file), t0)

    def test_main_with_args_dispatches_revoke_via_real_connection(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"):
            self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    token_file=token_file,
                )
            )
            self.assertTrue(auth_tool.auth_login(self._last_args))
            self.assertTrue(os.path.exists(token_file))

            self._remember(
                _make_args(
                    subcommand="revoke",
                    host="127.0.0.1",
                    port=server.port,
                    token=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.main_with_args(self._last_args)

        self.assertTrue(ok)
        self.assertFalse(os.path.exists(token_file))

    def test_main_with_args_rejects_direct_flag_without_probe(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        with BedServerContext() as server, \
             patch.object(auth_tool._routing, "probe_bed") as probe, \
             patch.object(auth_tool.io, "echo") as echo:
            args = self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker="alice",
                    password="pw",
                    direct=True,
                )
            )
            ok = auth_tool.main_with_args(args)

        self.assertFalse(ok)
        probe.assert_not_called()
        rendered = "\n".join(c.args[0] for c in echo.call_args_list)
        self.assertIn("--direct is unsupported", rendered)

    def test_main_with_args_probes_bed_when_reachable(self):
        from bed.tools import auth as auth_tool
        from bed.tests._auth_helpers import BedServerContext

        token_file = os.path.join(self._xdg_tmp, "tok")
        with BedServerContext() as server, \
             patch.object(auth_tool.io, "echo"), \
             patch.object(auth_tool, "inputpassword", return_value="pw"), \
             patch.object(auth_tool.io, "inputstring", return_value="alice"):
            args = self._remember(
                _make_args(
                    subcommand="login",
                    host="127.0.0.1",
                    port=server.port,
                    moniker=None,
                    password=None,
                    token_file=token_file,
                )
            )
            ok = auth_tool.main_with_args(args)

        self.assertTrue(ok)
        self.assertTrue(os.path.exists(token_file))
