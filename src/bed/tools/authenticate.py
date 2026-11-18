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
from bbsengine6.seam.requests import LoginRequest

from bed.tools import presentation, store, token_name


def _login(moniker: str, password: str) -> tuple[str, str, str]:
    """Send the login through the seam; return (status, token, moniker). The status is "ok" or a refusal code."""
    from bed.api.login import handle_login

    from bed.tools.login_service import login_service

    result = handle_login(login_service(), LoginRequest(moniker=moniker, password=password))
    if not result.ok:
        return result.code, "", moniker
    return result.code, str(result.data.get("token", "")), str(result.data.get("moniker", moniker))


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
