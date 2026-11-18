"""authenticate: the one call every front end makes to sign a player in.

It prompts for the moniker and password (``presentation.prompt_credentials``), sends a ``LoginRequest`` through the
seam's login handler, and stores the token for this player and terminal (``store.store_token``). A refused login stores
nothing. Front ends that do not keep a token (a headless script) can pass ``store=False``.

The older names (``auth_login``, the casino ``auth_prompt``, round-view's ``ensure_token``) are deprecated aliases of
this function and are removed in the next release.
"""

from __future__ import annotations

import os
import warnings
from typing import Optional

from bbsengine6.seam.codes import ErrorCode

from bed.tools import presentation, store, token_name


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


def authenticate(*, directory: str, title: str = "login", header: Optional[str] = None, fd: int = 0,
                 store_token: bool = True, moniker: str = "") -> tuple[str, str]:
    """Prompt, log in, and (when ``store_token``) store the token. Returns ``(moniker, token)``; token is "" on refusal."""
    name, password = presentation.prompt_credentials(title=title, header=header) if not moniker else (
        moniker, presentation.prompt_credentials(title=title, header=header)[1])
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
