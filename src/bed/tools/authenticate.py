"""authenticate: the one call every front end makes to sign a player in.

It prompts for the moniker and password (``presentation.prompt_credentials``), sends a ``LoginRequest`` through the
seam's login handler, and stores the token for this player and terminal (``store.store_token``). A refused login stores
nothing. Front ends that do not keep a token (a headless script) can pass ``store=False``.

The older names (``auth_login``, the casino ``auth_prompt``, round-view's ``ensure_token``) are deprecated aliases of
this function and are removed in the next release.
"""

from __future__ import annotations

import datetime as dt
import os
import warnings
from typing import Optional

from bbsengine6.seam.codes import ErrorCode

from bbsengine6 import io

from bed.tools import presentation, store, token_name


datetime = dt.datetime


_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def time_line() -> str:
    """The local time with its zone name, then UTC in parentheses: ``2026-11-20 14:03:22 EST (2026-11-20 19:03:22 UTC)``.

    Both values are formatted by bbsengine6 ``util.datestamp``.
    """
    from bbsengine6 import util

    local = datetime.now().astimezone()
    utc = local.astimezone(dt.timezone.utc)
    zone = local.tzname() or local.strftime("%z")
    return f"{util.datestamp(local, format=_TIME_FORMAT)} {zone} ({util.datestamp(utc, format=_TIME_FORMAT)} UTC)"


def ping_lines(pong: dict) -> list[str]:
    """The pong dict laid out one key per line, with the colour tokens, under a ``bed ping:`` header."""
    lines = ["bed ping:"]
    for key, value in pong.items():
        lines.append(f"{{labelcolor}}{key}: {{valuecolor}}{value}")
    return lines


def _bed_ping(args=None) -> dict:
    """Ping the running bed on the front end's endpoint, synchronously. Raises PingUnavailable when it is down."""
    import asyncio

    from bed.tools.ping import send_ping

    endpoint = args if args is not None else _default_bed_args()
    return asyncio.run(send_ping(endpoint.bed_host, endpoint.bed_port, path=endpoint.bed_path,
                                 timeout=endpoint.bed_call_timeout, prog="bed"))


def token_directory(args=None) -> str:
    """The directory holding the per-player token files: bed's own token directory (the parent of the default token)."""
    from bed.tools import _token

    return os.path.dirname(_token.default_token_path())


def _wire_login_client(args=None):
    """The client that logs in with the running bed, the same one ``bed auth login`` uses."""
    from bed.client.authservice import BedAuthServiceClient
    from bed.client.singleton import get_bed_connection

    return BedAuthServiceClient(get_bed_connection(args if args is not None else _default_bed_args()))


def _default_bed_args():
    """The bed endpoint for a front end: the defaults ``bed auth login`` uses (host, port, path, timeouts)."""
    import argparse

    return argparse.Namespace(bed_host="localhost", bed_port=8765, bed_path="/", bed_call_timeout=5.0,
                              bed_probe_timeout=0.25, debug=False)


def _login(moniker: str, password: str, args=None) -> tuple[str, str, str]:
    """Check the password with the running bed, over the wire. Returns (status, token, moniker).

    The password is checked by the daemon, which holds the member store. A front end never checks it locally, so it
    needs no database configuration of its own. The status is "ok" or the refusal code bed returned.
    """
    import asyncio

    svc = _wire_login_client(args)
    reply = asyncio.run(svc.login(moniker, password))
    if not reply.get("ok"):
        return str(reply.get("code") or "invalid_credentials"), "", moniker
    token = str(reply.get("token") or "")
    if not token:
        return "invalid_credentials", "", moniker
    return "ok", token, str(reply.get("moniker") or moniker)


def _echo_ping() -> None:
    """Echo the bed ping result at debug level. A down daemon is reported, and the sign-in continues."""
    from bed.tools.ping import PingUnavailable

    try:
        pong = _bed_ping()
    except PingUnavailable as exc:
        io.echo(f"bed ping: unavailable ({exc})", level="debug")
        return
    for line in ping_lines(pong):
        io.echo(line, level="debug")


def _presentation():
    """The login presentation named by bed.json ``auth.presentation``, resolved against the contract stub.

    The shipped value is the terminal presentation. A value that does not match the contract raises when resolved, with
    the key named, before any prompt.
    """
    from bbsengine6 import module as be6_module

    spec = _configured_auth_value("presentation", "bed.tools.presentation:prompt_credentials")
    try:
        return be6_module.resolve_callable(spec, be6_module._stub_prompt_credentials)
    except (ValueError, TypeError) as exc:
        raise type(exc)(f"auth.presentation in bed.json: {exc}") from exc


def _configured_auth_value(key: str, default: str) -> str:
    """The ``auth`` section value for ``key`` from the shipped bed.json, or ``default`` when it is absent."""
    import json
    from pathlib import Path

    import bed

    path = Path(bed.__file__).resolve().parent / "data" / "bed.json"
    auth_section = json.loads(path.read_text(encoding="utf-8")).get("auth", {})
    return auth_section.get(key) or default


def authenticate(*, directory: str, title: str = "login", header: Optional[str] = None, fd: int = 0,
                 store_token: bool = True, moniker: str = "", debug: bool = False, **kwargs) -> tuple[str, str]:
    """Prompt, log in, and (when ``store_token``) store the token. Returns ``(moniker, token)``; token is "" on refusal.

    ``debug=True`` echoes the bed URL, the terminal report, the local time (UTC in parentheses) and the bed ping result,
    all at level "debug". ``**kwargs`` is accepted so the configured callable (``auth.authenticate``) keeps matching the
    contract when a caller passes an option this version does not use. Extra options are ignored.
    """
    if debug:
        endpoint = _default_bed_args()
        io.echo(f"bed url: ws://{endpoint.bed_host}:{endpoint.bed_port}{endpoint.bed_path}", level="debug")
        io.echo(f"terminal: {io.terminal.report()}", level="debug")
        io.echo(f"time: {time_line()}", level="debug")
        _echo_ping()
    prompt = _presentation()
    name, password = prompt(title=title, header=header) if not moniker else (
        moniker, prompt(title=title, header=header)[1])
    status, token, logged_in = _login(name, password)
    if status != ErrorCode.OK.value or not token:
        return logged_in, ""
    if store_token:
        path = token_name.token_file_path(directory, logged_in, fd=fd)
        store.store_token(path, token)
    return logged_in, token


def auth_login_alias(*, directory: str, title: str = "login", header: Optional[str] = None, fd: int = 0) -> tuple[str, str]:
    """Deprecated: use ``authenticate``. Removed in the next release."""
    warnings.warn("auth_login is deprecated; use bed.tools.authenticate.authenticate", DeprecationWarning, stacklevel=2)
    return authenticate(directory=directory, title=title, header=header, fd=fd)
