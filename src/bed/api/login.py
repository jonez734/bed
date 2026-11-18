"""The login handler for the seam's ``auth`` scope (bbsengine6 section 7-auth).

This is the login core every front end shares: refuse a blank, check the credentials, mint a token, and record a
failed login. It returns an ActionResult. It does not bind a socket, read last-login, print, or write a token file;
those belong to the wire handler (session binding) and the front end (storing the token).
"""

from __future__ import annotations

import uuid
from typing import Any

from bbsengine6.seam.codes import ErrorCode
from bbsengine6.seam.requests import LoginRequest
from bbsengine6.seam.results import refusal, success


def handle_login(service: Any, request: LoginRequest):
    """Log a member in. ``service`` is the AuthService (its credential provider, token store and minting)."""
    moniker = (request.moniker or "").strip()
    password = request.password or ""
    if not moniker or not password:
        # A blank is never accepted: no credential check runs, and the front end is asked to fill both fields.
        return refusal(ErrorCode.INVALID_CREDENTIALS.value, "moniker and password are required")

    info = service.credential_provider.authenticate(
        service.args, moniker, password, pool=getattr(service.args, "pool", None)
    )
    if info is None:
        service._record_login_failed(moniker, None)
        return refusal(ErrorCode.INVALID_CREDENTIALS.value, "Invalid moniker or password")

    record = service._mint_record(info, str(uuid.uuid4()), "")
    service._persist(record)
    return success({"token": record.token, "moniker": info.moniker}, message="logged in")
