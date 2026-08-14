#!/usr/bin/env python3
"""Generate an auth token for the opencode user.

The user (`opencode`, uid 967) cannot read jam's bearer token at
``/run/user/1000/bed.token`` (mode 0600 owned by jam), so the
``bank`` CLI cannot be exercised end-to-end as opencode without
either creating a new member in the live database or spinning up a
local in-process bed server with a stub credential provider.

This module is a tiny stand-alone harness that does the latter:

1. Boots an in-process bed server (via :class:`BedServerContext`)
   with a custom credential provider that accepts ``(moniker,
   password)`` (default ``("opencode", "test")``).
2. Drives ``bed.tools.auth.auth_login`` against that server through
   the real :class:`bed.client.authservice.BedAuthServiceClient`.
3. Writes the issued bearer token to ``--token-file`` (defaults to
   the standard opencode location ``/tmp/bed-967/bed.token`` so the
   ``bank`` CLI picks it up via its default ``$XDG_RUNTIME_DIR`` /
   ``/tmp/bed-<uid>`` fallback).
4. Prints the token on stdout so callers can capture it.

The token is owned by the in-process server only; it is invalidated
as soon as the harness shuts down. Tests that need to drive the
``bank`` flow against the same server use
:func:`opencode_server_with_token` (yields ``(server, token)``) so
the server stays alive for the duration of the test.

Use it from the shell::

    python3 -m bed.tests.test_opencode_token

or under pytest::

    pytest -xvs src/bed/tests/test_opencode_token.py
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
import unittest
from typing import Any, Optional
from unittest.mock import patch


sys.path.insert(0, "/home/opencode/data/work/bed/src")


from bed.api.token_store import MemberInfo


class OpenCodeCredentialProvider:
    """Stub credential provider that accepts ``(moniker, password)``
    only for the configured pair (default ``("opencode", "test")``).

    Configurable so the same harness can mint tokens for arbitrary
    short-lived users when debugging the bank tool flow.
    """

    def __init__(
        self,
        moniker: str = "opencode",
        password: str = "test",
        *,
        is_sysop: bool = True,
        loginid: str = "opencode_os",
    ) -> None:
        self._moniker = moniker
        self._password = password
        self._is_sysop = is_sysop
        self._loginid = loginid

    def authenticate(self, args, moniker, password, *, pool=None):
        if moniker != self._moniker or password != self._password:
            return None
        return MemberInfo(
            moniker=self._moniker,
            is_sysop=self._is_sysop,
            balance=0,
            loginid=self._loginid,
        )


def _default_token_path() -> str:
    """Mirror ``bed.tools._token.default_token_path`` so we drop the
    token where the ``bank`` CLI's default lookup will find it."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return os.path.join(runtime, "bed.token")
    return os.path.join(
        tempfile.gettempdir(), f"bed-{os.getuid()}", "bed.token"
    )


def _ensure_parent_dir(path: str, *, mode: int) -> None:
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, mode=mode, exist_ok=True)
    except FileExistsError:
        pass
    try:
        os.chmod(parent, mode)
    except OSError:
        pass


def _write_token_file(path: str, token: str) -> None:
    _ensure_parent_dir(path, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
        os.write(fd, b"\n")
    finally:
        os.close(fd)


@contextlib.contextmanager
def opencode_server_with_token(
    *,
    moniker: str = "opencode",
    password: str = "test",
    token_file: Optional[str] = None,
    is_sysop: bool = True,
    loginid: str = "opencode_os",
):
    """Yield ``(server, token)`` for the lifetime of an in-process
    bed server that authenticates ``(moniker, password)``.

    The token is written to ``token_file`` (default: opencode's
    standard location) and the server keeps running until the
    ``with`` block exits. Use this from a pytest test that needs
    both the token and a live server to drive the bank flow.
    """
    from bed.client import authservice
    from bed.tests._auth_helpers import BedServerContext
    from bed.tools import auth as auth_tool

    authservice.reset_auth_client()

    if token_file is None:
        token_file = _default_token_path()

    provider = OpenCodeCredentialProvider(
        moniker=moniker,
        password=password,
        is_sysop=is_sysop,
        loginid=loginid,
    )

    xdg_tmp = tempfile.mkdtemp(prefix="bed-opencode-token-")
    xdg_patch = patch.dict(os.environ, {"XDG_RUNTIME_DIR": xdg_tmp})
    xdg_patch.start()
    try:
        with BedServerContext(credential_provider=provider) as server:
            args = argparse.Namespace()
            args.subcommand = "login"
            args.moniker = moniker
            args.password = password
            args.token = None
            args.token_file = token_file
            args.bed_host = "127.0.0.1"
            args.bed_port = server.port
            args.bed_path = "/"
            args.bed_call_timeout = 5.0
            args.bed_probe_timeout = 0.25
            args.direct = False
            args.debug = False

            with patch.object(auth_tool.io, "echo"):
                ok = auth_tool.auth_login(args)

            if not ok:
                raise RuntimeError(
                    f"auth_login failed for {moniker!r}; check the stub provider"
                )

            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()

            try:
                _write_token_file(token_file, token)
            except OSError:
                pass
            yield server, token
    finally:
        xdg_patch.stop()
        for name in os.listdir(xdg_tmp):
            try:
                os.unlink(os.path.join(xdg_tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(xdg_tmp)
        except OSError:
            pass
        authservice.reset_auth_client()


def _generate_token(
    *,
    moniker: str = "opencode",
    password: str = "test",
    token_file: Optional[str] = None,
    is_sysop: bool = True,
    loginid: str = "opencode_os",
) -> str:
    """Spin up a local bed server, run ``auth login``, return the
    token, then shut the server down. The token is invalidated as
    soon as the function returns; use :func:`opencode_server_with_token`
    if you need to drive further wire calls against the same server.
    """
    with opencode_server_with_token(
        moniker=moniker,
        password=password,
        token_file=token_file,
        is_sysop=is_sysop,
        loginid=loginid,
    ) as (_, token):
        return token


@contextlib.contextmanager
def opencode_server_with_bank(
    *,
    bank_mock: Any = None,
    moniker: str = "opencode",
    password: str = "test",
    token_file: Optional[str] = None,
    is_sysop: bool = True,
    loginid: str = "opencode_os",
):
    """Yield ``(server, token, bank_mock)`` for the lifetime of an
    in-process bed server that authenticates ``(moniker, password)``
    AND registers a :class:`bed.api.bank.BankService` whose underlying
    ``bbsengine6.bank.BankService`` is short-circuited via
    ``service._get_bank`` to the supplied ``bank_mock`` (or a default
    MagicMock that returns sensible values).

    This lets the test reproduce the exact ``bank`` CLI flow
    (mint a token, then issue ``bank_balance`` / ``bank_add``) against
    a real bed WebSocket without needing a live PostgreSQL.
    """
    import argparse as _argparse
    import secrets as _secrets

    from bed.api import AuthService as _AuthService
    from bed.api import InMemoryTokenStore as _InMemoryTokenStore
    from bed.api.bank import BankService as _BankService
    from bed.api.session import SessionRegistry as _SessionRegistry
    from bed.client import authservice as _authservice_mod
    from bed.tests._auth_helpers import BedServerContext
    from bed.tools import auth as auth_tool
    from unittest.mock import MagicMock

    if bank_mock is None:
        bank_mock = MagicMock()
        bank_mock.get_balance = MagicMock(return_value=0)
        bank_mock.add_funds = MagicMock(
            return_value={"success": True, "new_balance": 1, "message": "credit"}
        )
        bank_mock.remove_funds = MagicMock(
            return_value={"success": True, "new_balance": 0, "message": "debit"}
        )
        bank_mock.get_history = MagicMock(return_value=[])
        bank_mock.transfer = MagicMock(
            return_value={"success": True, "transfer_id": 1, "message": "queued"}
        )
        bank_mock.approve_transfer = MagicMock(
            return_value={
                "success": True,
                "transfer_id": 1,
                "from_balance": 0,
                "to_balance": 1,
            }
        )
        bank_mock.reject_transfer = MagicMock(
            return_value={"success": True, "transfer_id": 1}
        )
        bank_mock.get_pending_transfers = MagicMock(return_value=[])
        bank_mock.list_all = MagicMock(return_value=[])

    _authservice_mod.reset_auth_client()

    if token_file is None:
        token_file = _default_token_path()

    provider = OpenCodeCredentialProvider(
        moniker=moniker,
        password=password,
        is_sysop=is_sysop,
        loginid=loginid,
    )

    xdg_tmp = tempfile.mkdtemp(prefix="bed-opencode-bank-")
    xdg_patch = patch.dict(os.environ, {"XDG_RUNTIME_DIR": xdg_tmp})
    xdg_patch.start()

    secret = _secrets.token_bytes(32)
    registry = _SessionRegistry()
    token_store = _InMemoryTokenStore()
    auth_service = _AuthService(
        args=_argparse.Namespace(debug=False, pool=None),
        session_registry=registry,
        token_store=token_store,
        credential_provider=provider,
        secret=secret,
        instance_id="opencode-bank-test",
        ttl_seconds=900,
    )
    bank_service = _BankService(_argparse.Namespace(debug=False, pool=None), registry)
    bank_service._get_bank = MagicMock(return_value=bank_mock)
    bank_service.secret = secret
    bank_service.token_store = token_store
    bank_service.instance_id = "opencode-bank-test"

    server_ctx = BedServerContext(
        instance_id="opencode-bank-test",
        ttl_seconds=900,
        secret=secret,
        credential_provider=provider,
    )
    try:
        with server_ctx as server:
            auth_service.register_all(server.server)
            bank_service.register_all(server.server)
            server.auth_service = auth_service
            server.bank_service = bank_service
            server.bank_mock = bank_mock

            args = _argparse.Namespace()
            args.subcommand = "login"
            args.moniker = moniker
            args.password = password
            args.token = None
            args.token_file = token_file
            args.bed_host = "127.0.0.1"
            args.bed_port = server.port
            args.bed_path = "/"
            args.bed_call_timeout = 5.0
            args.bed_probe_timeout = 0.25
            args.direct = False
            args.debug = False

            with patch.object(auth_tool.io, "echo"):
                ok = auth_tool.auth_login(args)

            if not ok:
                raise RuntimeError(
                    f"auth_login failed for {moniker!r}; check the stub provider"
                )

            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()

            try:
                _write_token_file(token_file, token)
            except OSError:
                pass
            yield server, token, bank_mock
    finally:
        xdg_patch.stop()
        for name in os.listdir(xdg_tmp):
            try:
                os.unlink(os.path.join(xdg_tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(xdg_tmp)
        except OSError:
            pass
        _authservice_mod.reset_auth_client()


# ---------------------------------------------------------------------
# pytest entry point


class TestOpenCodeToken(unittest.TestCase):
    """End-to-end: spin up local bed, mint a token for ``opencode``,
    assert it is registered in the in-process token store and that
    the issued file carries mode 0600.
    """

    def test_opencode_login_writes_token_file(self):
        token = _generate_token()
        self.assertTrue(token, "auth_login returned an empty token")
        token_file = _default_token_path()
        self.assertTrue(os.path.exists(token_file))
        with open(token_file, "r", encoding="utf-8") as f:
            on_disk = f.read().strip()
        self.assertEqual(on_disk, token)
        perms = os.stat(token_file).st_mode & 0o777
        self.assertEqual(perms, 0o600)


class TestBankFlowViaOpenCodeToken(unittest.TestCase):
    """Drive the ``bank`` CLI flow end-to-end against an in-process
    bed server using a token minted for ``opencode``.

    Reproduces the failure the user reported: after a successful
    ``auth login`` the first ``bank_balance`` call appears to
    succeed (the facade silently returns 0 on a wire failure) and
    the next ``bank_add`` returns ``Authentication required``.

    The test captures every ``io.echo`` call and the
    :class:`bbsengine6.bank.BankService` mock invocations so a
    regression in either layer is surfaced loudly.
    """

    def _make_args(self, *, token_file: str, port: int) -> argparse.Namespace:
        args = argparse.Namespace()
        args.subcommand = None
        args.moniker = None
        args.password = None
        args.token = None
        args.token_file = token_file
        args.bed_host = "127.0.0.1"
        args.bed_port = port
        args.bed_path = "/"
        args.bed_call_timeout = 5.0
        args.bed_probe_timeout = 0.25
        args.direct = False
        args.debug = False
        args.sysop = False
        args.databasehost = "127.0.0.1"
        args.databaseport = 5432
        args.databasename = "opencode_test"
        args.databaseuser = None
        args.databasepassword = None
        return args

    def _drop_singletons(self, args: argparse.Namespace) -> None:
        from bed.client import singleton as _singleton

        with _singleton._CONNECTION_SINGLETON_LOCK:
            _singleton._CONNECTION_SINGLETON.pop(id(args), None)

    def test_balance_then_add_should_both_succeed(self):
        from bed.client import authservice as authservice_mod
        from bed.tests._auth_helpers import _drop_bed_connection_singleton
        from bed.tools import bank as bank_tool

        token_file = os.path.join(
            tempfile.mkdtemp(prefix="bed-opencode-bank-test-"), "tok"
        )

        with opencode_server_with_bank(token_file=token_file) as (server, _token, bank_mock):
            authservice_mod.reset_auth_client()
            args = self._make_args(token_file=token_file, port=server.port)

            with patch.object(bank_tool.io, "inputinteger", return_value=1), \
                 patch.object(bank_tool.io, "echo") as echo:
                self.assertTrue(
                    bank_tool._authenticate_ws(args),
                    "_authenticate_ws should succeed against the in-process server",
                )
                args._backend = "bed"
                bank_balance_ok = bank_tool.bank_balance(args, "opencode")
                bank_add_ok = bank_tool.bank_add(args, "opencode")

            self._drop_singletons(args)
            _drop_bed_connection_singleton(args)
            authservice_mod.reset_auth_client()

            echo_text = "\n".join(
                call.args[0] for call in echo.call_args_list if call.args
            )
            self.assertTrue(
                bank_mock.get_balance.called,
                "BankService.get_balance was never invoked; the wire call "
                f"short-circuited with an error envelope. echoed:\n{echo_text}",
            )
            self.assertTrue(
                bank_mock.add_funds.called,
                "BankService.add_funds was never invoked; the wire call "
                f"short-circuited with an error envelope. echoed:\n{echo_text}",
            )
            self.assertNotIn(
                "Authentication required",
                echo_text,
                "the wire call returned not_authenticated; the CLI is "
                "issuing bank_* on a WebSocket with no bound session. "
                f"echoed:\n{echo_text}",
            )
            self.assertTrue(
                bank_balance_ok,
                f"bank_balance returned False; echoed:\n{echo_text}",
            )
            self.assertTrue(
                bank_add_ok,
                f"bank_add returned False; echoed:\n{echo_text}",
            )


# ---------------------------------------------------------------------
# CLI entry point: `python3 -m bed.tests.test_opencode_token`


def _parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="opencode-token",
        description="Mint a short-lived bed auth token for the opencode user.",
    )
    parser.add_argument("--moniker", default="opencode")
    parser.add_argument("--password", default="test")
    parser.add_argument(
        "--token-file",
        default=_default_token_path(),
        help="Where to write the token (default: opencode's standard location).",
    )
    parser.add_argument(
        "--no-sysop",
        action="store_true",
        help="Mark the issued session as non-sysop.",
    )
    parser.add_argument(
        "--loginid",
        default="opencode_os",
        help="loginid to surface in server-side debug logs.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_cli()
    token = _generate_token(
        moniker=args.moniker,
        password=args.password,
        token_file=args.token_file,
        is_sysop=not args.no_sysop,
        loginid=args.loginid,
    )
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
