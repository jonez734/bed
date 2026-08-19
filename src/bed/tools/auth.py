"""Stand-alone auth operations script.

The CLI delegates transport to ``bed.client.authservice.BedAuthServiceClient``
(the same client bed.api.auth.AuthService talks to on the server side).
This keeps the protocol shape in one place: the auth module owns the
verb vocabulary (login, reconnect, refresh, revoke) and both the WS
service and the CLI ask it.

The CLI is bed-only: there is no direct backend because tokens are
HMAC-signed against a bed-side secret. ``--direct`` is rejected with a
clear error at startup.

The issued token is written to ``--token-file`` (default
``$XDG_RUNTIME_DIR/bed.token`` if set, else ``/tmp/bed-<uid>/bed.token``,
mode 0600) so other tools (``bank``) can pick it up without an
interactive prompt.
"""

import argparse
import asyncio
import os
import sys
from typing import Any, Optional

from bbsengine6 import io
from bbsengine6.util import inputpassword

from bed.client.authservice import BedAuthServiceClient
from bed.client.exceptions import BedUnavailable
from bed.tools import _routing
from bed.tools import _token


_DIRECT_UNSUPPORTED_MSG = (
    "auth only operates through the bed daemon; --direct is unsupported"
)


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    """Register the ``auth`` CLI flags on ``parentparser``."""
    _routing.build_client_args(parentparser)
    parentparser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    sub = parentparser.add_subparsers(dest="subcommand", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--moniker",
        default=None,
        help="Member moniker (defaults to interactive prompt)",
    )
    common.add_argument(
        "--password",
        default=None,
        help="Member password (defaults to masked interactive prompt)",
    )
    common.add_argument(
        "--token",
        default=None,
        help="Existing bearer token (overrides --token-file)",
    )
    _token.build_token_file_arg(common)
    sub.add_parser("login", parents=[common], help="Issue a fresh bearer token")
    sub.add_parser(
        "reconnect",
        parents=[common],
        help="Rebind an existing token to a new websocket",
    )
    sub.add_parser(
        "refresh",
        parents=[common],
        help="Rotate the token on the original websocket",
    )
    sub.add_parser(
        "revoke",
        parents=[common],
        help="Delete a token from the bed store and truncate the token file",
    )


def _default_token_path() -> str:
    return _token.default_token_path()


def _ensure_parent_dir(path: str, *, mode: int) -> None:
    _token._ensure_parent_dir(path, mode=mode)


def _check_token_file_perms(path: str) -> None:
    _token.check_token_file_perms(path)


def _write_token_file(path: str, token: str) -> None:
    """Atomically write ``token`` to ``path`` (mode 0600)."""
    _ensure_parent_dir(path, mode=0o700)
    if os.path.exists(path):
        _check_token_file_perms(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
        os.write(fd, b"\n")
    finally:
        os.close(fd)


def _read_token_file(path: str) -> str:
    """Return the token stored in ``path`` (``""`` if missing/empty).

    Thin wrapper around :func:`bed.tools._token.read_token_file`;
    kept as a module-private alias so existing callers don't change.
    """
    return _token.read_token_file(path)


def _truncate_token_file(path: str) -> None:
    """Delete the token file if it exists. Idempotent."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _resolve_token(args) -> str:
    """Return the token to use for reconnect/refresh/revoke.

    Thin wrapper around :func:`bed.tools._token.resolve_token`;
    kept as a module-private alias so existing callers don't change.
    """
    return _token.resolve_token(args)


def _ensure_token_file_arg(args) -> None:
    """Populate ``args.token_file`` with the default path if absent.

    Thin wrapper around :func:`bed.tools._token.ensure_token_file_arg`;
    kept as a module-private alias so existing callers don't change.
    """
    _token.ensure_token_file_arg(args)


def _auth_service(args: Any) -> BedAuthServiceClient:
    """Build the :class:`BedAuthServiceClient` for ``args``."""
    from bed.client import get_bed_connection

    return BedAuthServiceClient(get_bed_connection(args))


def _render_soft_failure(reply: dict) -> None:
    """Print a one-line error from a soft-failure reply dict."""
    code = reply.get("code") or "unknown"
    message = reply.get("message") or ""
    io.echo(f"{code}: {message}".rstrip(), level="error")


_TOKEN_RESPONSE_REQUIRED_FIELDS = ("token", "session_id", "expires_at")


def _check_token_response(reply: dict) -> list[str]:
    """Return the names of required fields missing from a token-bearing reply.

    Every token-bearing reply (``login``, ``reconnect``, ``refresh``)
    carries the same three fields: ``token``, ``session_id``,
    ``expires_at``. If the server replies with ``ok=True`` but any of
    these is empty, the response is malformed and the CLI must refuse
    to write the token file -- otherwise the next ``auth reconnect``
    would fail with a misleading ``missing_token`` error against an
    empty file.
    """
    return [
        name
        for name in _TOKEN_RESPONSE_REQUIRED_FIELDS
        if not reply.get(name, "")
    ]


def _reject_malformed_token_response(reply: dict) -> bool:
    """Emit the malformed-token-response error if ``reply`` is incomplete.

    Returns True if ``reply`` was rejected (caller should return False).
    Returns False if ``reply`` carries all required fields.
    """
    missing = _check_token_response(reply)
    if not missing:
        return False
    io.echo(
        "server returned a malformed auth_result (missing: "
        + ", ".join(missing)
        + "); refusing to write token file",
        level="error",
    )
    return True


def auth_login(args) -> bool:
    _ensure_token_file_arg(args)
    moniker = (getattr(args, "moniker", None) or "").strip()
    if not moniker:
        moniker = io.inputstring("moniker: ", "", args=args).strip()
    if not moniker:
        io.echo("moniker is required", level="error")
        return False
    password = (getattr(args, "password", None) or "").strip()
    if not password:
        password = inputpassword("password: ").strip()
    if not password:
        io.echo("password is required", level="error")
        return False
    svc = _auth_service(args)
    reply = asyncio.run(svc.login(moniker, password))
    if not reply.get("ok"):
        _render_soft_failure(reply)
        return False
    if _reject_malformed_token_response(reply):
        return False
    token = reply.get("token", "")
    session_id = reply.get("session_id", "")
    expires_at = reply.get("expires_at", "")
    io.echo(
        f"issued token for moniker={reply.get('moniker', moniker)!r} "
        f"session_id={session_id[:8]}… "
        f"expires_at={expires_at} "
        f"is_sysop={bool(reply.get('is_sysop', False))}"
    )
    try:
        _write_token_file(args.token_file, token)
    except (OSError, PermissionError) as e:
        io.echo(f"could not write token file {args.token_file}: {e}", level="error")
        return False
    io.echo(f"token written to {args.token_file}")
    return True


def auth_reconnect(args) -> bool:
    _ensure_token_file_arg(args)
    token = _resolve_token(args)
    if not token:
        io.echo("missing_token: token is required", level="error")
        return False
    svc = _auth_service(args)
    reply = asyncio.run(svc.reconnect(token))
    if not reply.get("ok"):
        _render_soft_failure(reply)
        return False
    if _reject_malformed_token_response(reply):
        return False
    new_token = reply.get("token", "")
    io.echo(
        f"reconnected moniker={reply.get('moniker', '')!r} "
        f"session_id={reply.get('session_id', '')[:8] or '<none>'}… "
        f"expires_at={reply.get('expires_at', '')} "
        f"is_sysop={bool(reply.get('is_sysop', False))} "
        f"replayed={'yes' if reply.get('replayed') else 'no'}"
    )
    try:
        _write_token_file(args.token_file, new_token)
    except (OSError, PermissionError) as e:
        io.echo(f"could not write token file {args.token_file}: {e}", level="error")
        return False
    io.echo(f"token written to {args.token_file}")
    return True


def auth_refresh(args) -> bool:
    _ensure_token_file_arg(args)
    token = _resolve_token(args)
    if not token:
        io.echo("missing_token: token is required", level="error")
        return False
    svc = _auth_service(args)
    reply = asyncio.run(svc.refresh(token))
    if not reply.get("ok"):
        _render_soft_failure(reply)
        return False
    if _reject_malformed_token_response(reply):
        return False
    new_token = reply.get("token", "")
    io.echo(
        f"refreshed moniker={reply.get('moniker', '')!r} "
        f"session_id={reply.get('session_id', '')[:8] or '<none>'}… "
        f"expires_at={reply.get('expires_at', '')}"
    )
    try:
        _write_token_file(args.token_file, new_token)
    except (OSError, PermissionError) as e:
        io.echo(f"could not write token file {args.token_file}: {e}", level="error")
        return False
    io.echo(f"token written to {args.token_file}")
    return True


def auth_revoke(args) -> bool:
    _ensure_token_file_arg(args)
    token = _resolve_token(args)
    if not token:
        io.echo("missing_token: token is required", level="error")
        return False
    svc = _auth_service(args)
    reply = asyncio.run(svc.revoke(token))
    if not reply.get("ok"):
        _render_soft_failure(reply)
        return False
    io.echo("token revoked")
    _truncate_token_file(args.token_file)
    return True


def main_with_args(args) -> Optional[bool]:
    """Run the auth subcommand against a pre-parsed args object.

    Split out from ``main()`` so tests can drive the CLI without going
    through argparse. Returns the subcommand's success flag, or
    ``None`` for early exits (BedNotReachable / --direct guard).

    Connection-level failures are caught in two places:

    - :class:`bed.tools._routing.BedNotReachable` from the TCP probe
      in :func:`_routing.select_backend` (daemon not listening).
    - :class:`bed.client.BedUnavailable` from the WebSocket send /
      recv path in :class:`bed.client.BedConnection` (daemon went
      away mid-session, dropped connection, post-handshake error).
    Both render via :func:`bbsengine6.io.echo` with ``level="error"``
    so no Python traceback reaches the operator.
    """
    if getattr(args, "direct", False):
        io.echo(_DIRECT_UNSUPPORTED_MSG, level="error")
        return False

    try:
        _routing.select_backend(args)
    except _routing.BedNotReachable as e:
        io.echo(str(e), level="error")
        return False

    sub = getattr(args, "subcommand", None)
    try:
        if sub == "login":
            return auth_login(args)
        if sub == "reconnect":
            return auth_reconnect(args)
        if sub == "refresh":
            return auth_refresh(args)
        if sub == "revoke":
            return auth_revoke(args)
    except KeyboardInterrupt:
        io.echo("{/all}{restorecursor}*INTR*")
        return False
    except EOFError:
        io.echo("{/all}{restorecursor}*EOF*")
        return False
    except BedUnavailable as e:
        io.echo(str(e), level="error")
        return False
    io.echo(f"unknown subcommand {sub!r}", level="error")
    return False


def main() -> None:
    parser = argparse.ArgumentParser("auth")
    buildargs(parser)
    args = parser.parse_args()
    io.echo(f"{args=}", level="debug")
    ok = main_with_args(args)
    if ok is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
