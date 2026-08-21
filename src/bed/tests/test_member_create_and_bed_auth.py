#!/usr/bin/env python3
"""End-to-end create-member + bed-auth integration tests.

Mirrors ``casino/src/casino/tests/test_member_create_and_casino_auth.py``
but exercises the round-trip through bed's own CLI surface instead of
casino's. Two halves:

(a) Create a new member and set a simple plaintext password.
    Two paths:
      1. ``bbsengine6.member.setpassword`` (the public API the
         console uses; ``setpassword`` issues ``UPDATE
         engine.__member SET password = crypt($1, gen_salt('bf'))``).
      2. Raw ``INSERT ... crypt('pw', gen_salt('bf'))`` (the path
         ``test_blackjack_flow.py`` uses directly).
    Each path round-trips the plaintext through
    ``bbsengine6.member.checkpassword``.

(b) Drive bed's moniker + password prompt CLI
    (:func:`bed.tools.auth.auth_login`) end-to-end against an
    in-process bed server whose
    :class:`bed.api.credential_provider.PasswordCredentialProvider`
    calls ``bbsengine6.member.checkpassword`` for real. The same
    (moniker, password) created in (a) succeeds: a bearer token lands
    in ``args.token_file`` and the in-process server's token store
    carries a matching record.

The (b) half covers two prompt strategies:

  - **Prompts**: leave ``--moniker`` / ``--password`` off the args;
    ``auth_login`` then reads them via
    ``io.inputstring`` / ``util.inputpassword`` (patched).
  - **Explicit flags**: pass ``--moniker`` / ``--password``; no
    prompting.

Skips cleanly when ``engine.__member`` isn't reachable.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import secrets
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


import pytest


sys.path.insert(0, "/home/opencode/data/work/bbsengine6/py/src")
sys.path.insert(0, "/home/opencode/data/work/bed/src")


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------
# Helpers shared across all three test classes.


def _build_db_args() -> argparse.Namespace:
    """Build a fully-populated args namespace for bbsengine6 ops.

    Uses ``bbsengine6.console.lib.buildargs`` so
    ``args.databaseschema = "engine"`` (the bbsengine6
    ``_qualified`` helper reads this to expand ``$engine.member``).
    The ``--databasename`` is honoured from
    ``BBSENGINE6_DBNAME`` / ``--databasename`` so the test never
    hardcodes ``zoid6``.
    """
    import bbsengine6.console.lib as con_lib

    parser = con_lib.buildargs()
    return parser.parse_args(["--databasename", os.environ.get("BBSENGINE6_DBNAME", "zoid6")])


def _member_table_reachable(args) -> bool:
    """True iff ``engine.__member`` (or ``args.databaseschema`` equivalent) is queryable."""
    from bbsengine6 import database

    schema = getattr(args, "databaseschema", "engine") or "engine"
    try:
        pool = database.getpool(args)
        try:
            with database.connect(args, pool=pool) as conn, database.cursor(conn) as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = '__member' LIMIT 1",
                    (schema,),
                )
                return cur.fetchone() is not None
        finally:
            pool.close()
    except Exception:
        return False


def _make_unique_moniker(label: str) -> str:
    """Short unique test moniker so reruns against a dirty DB don't collide."""
    return f"{label}_{secrets.token_hex(3)}"


def _drop_member(args, pool, moniker: str) -> None:
    from bbsengine6 import database

    try:
        with database.connect(args, pool=pool) as conn, database.cursor(conn) as cur:
            cur.execute(
                "DELETE FROM engine.map_member_flag WHERE moniker = %s",
                (moniker,),
            )
            cur.execute(
                "DELETE FROM engine.__member WHERE moniker = %s",
                (moniker,),
            )
    except Exception:
        pass


# ---------------------------------------------------------------------
# (a) Create a new member and set a simple password.
#
# Both paths land in the same table; the difference is whether we go
# through the bbsengine6 public API or hand-roll the crypt() call.


class TestCreateMemberAndSetPassword(unittest.IsolatedAsyncioTestCase):
    """Step (a): create a new member, set a password, round-trip through checkpassword."""

    async def asyncSetUp(self):
        from bbsengine6 import database
        from bbsengine6 import member as libmember

        self.args = _build_db_args()
        self.libmember = libmember

        if not _member_table_reachable(self.args):
            self.skipTest("engine.__member not reachable on this DB")

        self.pool = database.getpool(self.args)
        self.moniker = _make_unique_moniker("alice_bed_test")
        self.password = "pw"

    async def asyncTearDown(self):
        if not hasattr(self, "moniker"):
            return
        _drop_member(self.args, self.pool, self.moniker)
        with contextlib.suppress(Exception):
            self.pool.close()

    async def test_a1_create_via_member_setpassword(self):
        """Path A: insert member with NULL password, then call
        ``bbsengine6.member.setpassword``. ``checkpassword`` round-trips True.
        """
        from bbsengine6 import database

        with database.connect(self.args, pool=self.pool) as conn, database.cursor(conn) as cur:
            cur.execute(
                "INSERT INTO engine.__member "
                "(moniker, loginid, email, credits, attrs) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (moniker) DO UPDATE SET "
                "loginid = EXCLUDED.loginid, "
                "email = EXCLUDED.email, "
                "credits = EXCLUDED.credits, "
                "attrs = EXCLUDED.attrs",
                (
                    self.moniker,
                    self.moniker,
                    f"{self.moniker}@test.local",
                    1000,
                    "{}",
                ),
            )

        result = self.libmember.setpassword(
            self.args, self.password, self.moniker, pool=self.pool
        )
        self.assertIs(
            result,
            True,
            f"setpassword returned {result!r}; expected True",
        )

        self.assertIs(
            self.libmember.has_password(
                self.args, self.moniker, pool=self.pool
            ),
            True,
            "has_password should report True after setpassword",
        )
        self.assertIs(
            self.libmember.checkpassword(
                self.args, self.password, membermoniker=self.moniker,
                pool=self.pool,
            ),
            True,
            "checkpassword should round-trip the plaintext we just set",
        )

    async def test_a2_create_via_raw_crypt_sql(self):
        """Path B: insert with ``password = crypt('pw', gen_salt('bf'))``
        inline. Same two assertions as A: checkpassword / has_password True.
        """
        from bbsengine6 import database

        with database.connect(self.args, pool=self.pool) as conn, database.cursor(conn) as cur:
            cur.execute(
                "INSERT INTO engine.__member "
                "(moniker, loginid, password, email, credits, attrs) "
                "VALUES (%s, %s, crypt(%s, gen_salt('bf')), %s, %s, %s::jsonb) "
                "ON CONFLICT (moniker) DO UPDATE SET "
                "password = crypt(%s, gen_salt('bf'))",
                (
                    self.moniker,
                    self.moniker,
                    self.password,
                    f"{self.moniker}@test.local",
                    1000,
                    "{}",
                    self.password,
                ),
            )

        self.assertIs(
            self.libmember.has_password(
                self.args, self.moniker, pool=self.pool
            ),
            True,
            "has_password should report True after raw crypt insert",
        )
        self.assertIs(
            self.libmember.checkpassword(
                self.args, self.password, membermoniker=self.moniker,
                pool=self.pool,
            ),
            True,
            "checkpassword should round-trip the plaintext inserted via raw SQL",
        )


# ---------------------------------------------------------------------
# (b) bed CLI auth login -- prompt path and explicit-flag path, against
# an in-process bed server using the REAL PasswordCredentialProvider.
#
# Mirrors the singleton/XDG-tmpdir scaffolding in
# test_auth_tool_integration.py:_AuthToolTestBase but keeps it local so
# this file stays self-contained.


# ---------------------------------------------------------------------
# (b) bed CLI auth login -- prompt path and explicit-flag path, against
# an in-process bed server using the REAL PasswordCredentialProvider.
#
# Mirrors the singleton/XDG-tmpdir scaffolding in
# test_auth_tool_integration.py:_AuthToolTestBase but keeps it local so
# this file stays self-contained.


class TestBedAuthLoginEndToEnd(unittest.TestCase):
    """Step (b): drive :func:`bed.tools.auth.auth_login` end-to-end through bed.

    Creates a member via bbsengine6.member then logs in through bed's
    CLI; the in-process server's PasswordCredentialProvider calls
    bbsengine6.member.checkpassword so the round-trip we just set up
    is exactly what the server will verify.
    """

    SERVER_START_TIMEOUT = 2.0
    WS_RECV_TIMEOUT = 1.0

    def setUp(self) -> None:
        # XDG_RUNTIME_DIR -> tmpdir so bed's default token-file path
        # lands here (mirrors _AuthToolTestBase.setUp at
        # test_auth_tool_integration.py:89-98).
        self._xdg_tmp = tempfile.mkdtemp(prefix="bed-member-auth-test-")
        self._xdg_patch = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self._xdg_tmp})
        self._xdg_patch.start()

        from bed.client import authservice

        authservice.reset_auth_client()
        self._last_args: argparse.Namespace | None = None

        # Async portion: build DB args, create member, spin up
        # in-process bed server. asyncio.run propagates skipTest
        # cleanly so an unreachable DB shows up as a unittest skip
        # rather than a half-initialised setUp.
        try:
            asyncio.run(self._async_setup())
        except unittest.SkipTest:
            raise
        except BaseException:
            # If async_setup blew up halfway, try to clean up the
            # bits it managed to set up.
            self.tearDown()
            raise

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

        if hasattr(self, "_bed_ctx") and self._bed_ctx is not None:
            try:
                self._bed_ctx.__exit__(None, None, None)
            except Exception:
                pass
            self._bed_ctx = None

        if hasattr(self, "moniker") and hasattr(self, "args") and hasattr(self, "pool"):
            _drop_member(self.args, self.pool, self.moniker)

        if hasattr(self, "pool"):
            with contextlib.suppress(Exception):
                self.pool.close()

    def _remember(self, args: argparse.Namespace) -> argparse.Namespace:
        from bed.tests._auth_helpers import _drop_bed_connection_singleton

        if self._last_args is not None and self._last_args is not args:
            _drop_bed_connection_singleton(self._last_args)
        self._last_args = args
        return args

    async def _async_setup(self):
        """Async portion of setUp: build DB args, create the member,
        spin up an in-process bed server. The DB-reachability skip
        decision lives here so unittest sees a clean skip rather than
        a half-initialised setUp.
        """
        from bbsengine6 import database
        from bed.api import AuthService, InMemoryTokenStore
        from bed.api.credential_provider import PasswordCredentialProvider
        from bed.api.session import SessionRegistry
        from bbsengine6.net import WebSocketServer
        import socket as _socket
        import threading

        self.args = _build_db_args()
        if not _member_table_reachable(self.args):
            self.skipTest("engine.__member not reachable on this DB")
        self.pool = database.getpool(self.args)
        self.moniker = _make_unique_moniker("alice_bed_e2e")
        self.password = "pw"

        # Create the member via path (a.1) by default; per-test
        # methods can override (e.g. test_b2 uses path (a.2)).
        self._create_member_setpassword_path(self.moniker, self.password)

        # Spin up an in-process bed server with the REAL
        # PasswordCredentialProvider. The provider calls
        # bbsengine6.member.checkpassword against this DB so the
        # round-trip we just set up is exactly what the server will
        # verify.
        self._bed_loop = asyncio.new_event_loop()
        self._bed_thread = threading.Thread(
            target=self._bed_loop.run_forever,
            daemon=True,
            name="bed-test-member-auth",
        )
        self._bed_thread.start()

        secret = secrets.token_bytes(32)
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]

        registry = SessionRegistry()
        token_store = InMemoryTokenStore()
        auth_service = AuthService(
            args=self.args,  # has databaseschema="engine" so PasswordCredentialProvider.checkpassword works
            session_registry=registry,
            token_store=token_store,
            credential_provider=PasswordCredentialProvider(),
            secret=secret,
            instance_id="bed-member-auth-test",
            ttl_seconds=900,
        )

        server = WebSocketServer(host="127.0.0.1", port=self.port)
        auth_service.register_all(server)
        try:
            future = asyncio.run_coroutine_threadsafe(server.start(), self._bed_loop)
            future.result(timeout=self.SERVER_START_TIMEOUT)
        except BaseException:
            self._stop_bed_server(server)
            raise

        self._bed_ctx = _BedShutdownHandle(
            server=server,
            loop=self._bed_loop,
            thread=self._bed_thread,
        )
        self.auth_service = auth_service
        self.token_store = token_store

    def _create_member_setpassword_path(self, moniker: str, password: str) -> None:
        """Create member using the (a.1) public API path."""
        from bbsengine6 import database
        from bbsengine6 import member as libmember

        with database.connect(self.args, pool=self.pool) as conn, database.cursor(conn) as cur:
            cur.execute(
                "INSERT INTO engine.__member "
                "(moniker, loginid, email, credits, attrs) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (moniker) DO UPDATE SET "
                "loginid = EXCLUDED.loginid, "
                "email = EXCLUDED.email, "
                "credits = EXCLUDED.credits, "
                "attrs = EXCLUDED.attrs",
                (
                    moniker,
                    moniker,
                    f"{moniker}@test.local",
                    1000,
                    "{}",
                ),
            )
        self.assertIs(
            libmember.setpassword(self.args, password, moniker, pool=self.pool),
            True,
        )

    def _create_member_raw_crypt_path(self, moniker: str, password: str) -> None:
        """Create member using the (a.2) raw crypt() SQL path."""
        from bbsengine6 import database

        with database.connect(self.args, pool=self.pool) as conn, database.cursor(conn) as cur:
            cur.execute(
                "INSERT INTO engine.__member "
                "(moniker, loginid, password, email, credits, attrs) "
                "VALUES (%s, %s, crypt(%s, gen_salt('bf')), %s, %s, %s::jsonb) "
                "ON CONFLICT (moniker) DO UPDATE SET "
                "password = crypt(%s, gen_salt('bf'))",
                (
                    moniker,
                    moniker,
                    password,
                    f"{moniker}@test.local",
                    1000,
                    "{}",
                    password,
                ),
            )

    def _make_cli_args(
        self,
        *,
        moniker: str | None,
        password: str | None,
        token_file: str,
    ) -> argparse.Namespace:
        """Build the args namespace that ``bed.tools.auth.auth_login`` expects."""
        args = argparse.Namespace()
        args.subcommand = "login"
        args.moniker = moniker
        args.password = password
        args.token = None
        args.token_file = token_file
        args.bed_host = "127.0.0.1"
        args.bed_port = self.port
        args.bed_path = "/"
        args.bed_call_timeout = 0.5
        args.bed_probe_timeout = 0.25
        args.direct = False
        args.debug = False
        return args

    def _assert_login_succeeded(self, token_file: str) -> str:
        """Common post-login assertions: token file written, mode 0600,
        server's token store carries a record for our moniker.
        Returns the token contents so callers can assert further.
        """
        self.assertTrue(os.path.exists(token_file), f"token file {token_file!r} missing")
        with open(token_file) as f:
            token = f.read().strip()
        self.assertTrue(token, f"token file {token_file!r} is empty")

        # bed writes the token file with mode 0600
        self.assertEqual(
            stat.S_IMODE(os.stat(token_file).st_mode),
            0o600,
            "token file should be mode 0600",
        )

        # The in-process server's token store should hold a record
        # whose moniker matches our test moniker.
        record = self.token_store.get(token)
        self.assertIsNotNone(record, "server's token store has no record for the issued token")
        self.assertEqual(record.moniker, self.moniker)
        self.assertFalse(record.is_sysop)
        return token

    def test_b1_login_via_prompts_setpassword_path(self):
        """(b.1) Drive bed's auth_login CLI without ``--moniker`` /
        ``--password`` flags. auth_login prompts via io.inputstring /
        util.inputpassword; patches feed our test (moniker, password)
        into those prompts. Server (real PasswordCredentialProvider)
        accepts.
        """
        from bed.tools import auth as auth_tool

        token_file = os.path.join(self._xdg_tmp, "tok_b1")
        args = self._remember(self._make_cli_args(moniker=None, password=None, token_file=token_file))

        with patch.object(auth_tool.io, "echo"), \
             patch.object(auth_tool, "inputpassword", return_value=self.password), \
             patch.object(auth_tool.io, "inputstring", return_value=self.moniker):
            ok = auth_tool.auth_login(args)

        self.assertTrue(ok, "auth_login should return True on success")
        self._assert_login_succeeded(token_file)

    def test_b2_login_via_prompts_raw_crypt_sql_path(self):
        """(b.2) Same prompt path but the member was created via the
        (a.2) raw crypt() SQL path so the e2e flow covers both create
        paths symmetrically.
        """
        from bed.tools import auth as auth_tool

        # Recreate the member via path (a.2); tearDown will drop the
        # new moniker.
        _drop_member(self.args, self.pool, self.moniker)
        self.moniker = _make_unique_moniker("alice_bed_e2e_b2")
        self._create_member_raw_crypt_path(self.moniker, self.password)

        token_file = os.path.join(self._xdg_tmp, "tok_b2")
        args = self._remember(self._make_cli_args(moniker=None, password=None, token_file=token_file))

        with patch.object(auth_tool.io, "echo"), \
             patch.object(auth_tool, "inputpassword", return_value=self.password), \
             patch.object(auth_tool.io, "inputstring", return_value=self.moniker):
            ok = auth_tool.auth_login(args)

        self.assertTrue(ok, "auth_login should return True on success")
        self._assert_login_succeeded(token_file)

    def test_b3_login_via_explicit_flags_setpassword_path(self):
        """(b.3) Drive bed's auth_login with explicit ``--moniker`` /
        ``--password`` flags (no prompting). auth_login still walks
        the full BedAuthServiceClient -> bed WebSocket -> AuthService
        -> PasswordCredentialProvider -> member.checkpassword path.
        """
        from bed.tools import auth as auth_tool

        token_file = os.path.join(self._xdg_tmp, "tok_b3")
        args = self._remember(
            self._make_cli_args(moniker=self.moniker, password=self.password, token_file=token_file)
        )

        with patch.object(auth_tool.io, "echo"):
            ok = auth_tool.auth_login(args)

        self.assertTrue(ok, "auth_login should return True on success")
        self._assert_login_succeeded(token_file)


# ---------------------------------------------------------------------
# Local shutdown helper -- mirrors BedServerContext at
# bed/src/bed/tests/_auth_helpers.py:228-429 but trims what these
# tests need (no secret, no token_store lifecycle).


class _BedShutdownHandle:
    """Holds references to the in-process bed server so tearDown can
    stop it cleanly.

    Stopping is delegated to :meth:`_stop_bed_server` (a free function
    defined just below) because the cancel-everything-on-the-loop
    pattern needs the live server reference, which we discard here.
    """

    def __init__(self, server, loop, thread):
        self.server = server
        self.loop = loop
        self.thread = thread

    def __exit__(self, exc_type, exc, tb):
        _stop_bed_server(self.server, self.loop, self.thread)


def _stop_bed_server(server, loop, thread) -> None:
    """Cancel every task on the server loop and join the thread.

    Mirrors BedServerContext._safe_shutdown at
    bed/src/bed/tests/_auth_helpers.py:312-429 -- see that method's
    docstring for why we skip ``WebSocketServer.stop()`` and cancel
    loop tasks instead.
    """
    if loop is None:
        return
    try:
        future = asyncio.run_coroutine_threadsafe(
            _shutdown_bed_server(server), loop
        )
        future.result(timeout=2.0)
    except BaseException:
        pass
    try:
        loop.call_soon_threadsafe(loop.stop)
    except RuntimeError:
        pass
    if thread is not None:
        thread.join(timeout=2.0)
    try:
        loop.close()
    finally:
        pass


async def _shutdown_bed_server(server) -> None:
    if server is None:
        return
    ws_server = getattr(server, "_server", None)
    if ws_server is not None:
        try:
            asyncio_server = getattr(ws_server, "server", None)
            if asyncio_server is not None:
                asyncio_server.close()
        except Exception:
            pass
        try:
            connections = list(ws_server.connections)
        except Exception:
            connections = []
        for conn in connections:
            try:
                transport = getattr(conn, "transport", None)
                if transport is not None:
                    transport.close()
            except Exception:
                pass
    await asyncio.sleep(0)
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
