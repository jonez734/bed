#!/usr/bin/env python3
"""Regression tests for the BED-mode-vs-door-mode diagnostic.

The canonical diagnostic trick: send ``auth_refresh`` with a
malformed token (``bogus.bogus``) and read the ``code`` field
in the reply.

- **BED mode** (AuthService registered, ``token_persistence != none``):
  the message reaches ``AuthService._handle_auth_refresh``, which
  calls ``_decode_token`` → ``TokenError(CODE_TOKEN_INVALID)`` →
  reply envelope ``{"type": "error", "code": "token_invalid"}``.
  This proves the crypto wiring (``secret`` + ``token_store`` +
  ``instance_id``) is live on the wire.

- **Door mode** (no AuthService, no router registered, the
  legacy ``bbsengine6.net.defaultrouter.DefaultRouter`` shape
  with ``token_persistence=none``): the WebSocketServer has no
  service for ``auth_refresh`` and falls through to the
  ``"No handler - echo back"`` branch at
  ``bbsengine6/net/transport.py:969-971``. The reply is the
  raw incoming message echoed back. (NB: this is a debugging
  aid, not a contract — the bare-server echo behavior is
  intentionally permissive.)

The end-to-end casino-side variant: send ``create_table`` with
a bogus token to a live zoid6 daemon. BED mode returns
``code=token_invalid`` (signature failure on
``casino.api._auth.check_access``); pre-fix door mode returned
``code=not_authenticated`` (session not bound to bed's
``SessionRegistry``). The casino-side probe is documented in
``casino/docs/AUTH.md`` §7 and is the canonical user-facing
diagnostic. The ``auth_refresh`` probe in this file is the
in-process equivalent — it works against any BED-mode daemon
without needing a casino router registered.

Use ``unittest.IsolatedAsyncioTestCase`` so each test gets a
fresh event loop; otherwise the in-process server loop and
the test loop trip over each other on the ``websockets``
handshake.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import unittest
from typing import Any, Dict


sys.path.insert(0, "/home/opencode/data/work/bed/src")


# Shared helpers (sibling module — see _auth_helpers.py docstring).
from bed.tests._auth_helpers import (  # noqa: E402
    _send_and_recv,
    _start_bed_with_auth,
)


_BOGUS_TOKEN = "bogus.bogus"


class TestBedModeAuthRefreshDiagnostic(unittest.IsolatedAsyncioTestCase):
    """``code=token_invalid`` proves AuthService crypto wiring is live."""

    async def test_auth_refresh_with_bogus_token_returns_token_invalid(self):
        """``auth_refresh`` with a malformed token returns
        ``code=token_invalid`` when AuthService is registered. The
        token is malformed (``bogus.bogus`` is not
        ``<payload_b64>.<hmac_hex>``), so ``_decode_token`` raises
        ``TokenError(CODE_TOKEN_INVALID)`` at
        ``bed/api/auth.py:106`` (missing second ``.``-separated
        part). That maps directly to the wire reply.

        This pins the BED-mode path: any future regression that
        strips AuthService from the registration order (or breaks
        the dispatch path) will fail this test before the live
        smoke probe can show the wrong reply.
        """
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws, {"type": "auth_refresh", "token": _BOGUS_TOKEN}
                )
        finally:
            await server.stop()

        self.assertEqual(reply.get("type"), "error")
        self.assertEqual(
            reply.get("code"),
            "token_invalid",
            "BED mode expected; got an unexpected error code: "
            f"{reply!r}",
        )

    async def test_auth_refresh_with_well_formed_but_invalid_signature_returns_token_invalid(self):
        """A token with the correct ``<payload>.<sig>`` shape but a
        bad HMAC still fails at signature check
        (``bed/api/auth.py:114``: ``"bad signature"``), which is
        also ``CODE_TOKEN_INVALID``. This pins the second branch
        of ``_decode_token`` so a future refactor that swallows
        signature errors into a different ``code`` trips the test.
        """
        import websockets

        # ``aGVsbG8=<payload>`` shape with a junk signature.
        # ``aGVsbG8=`` is the urlsafe-b64 of ``"hello"``.
        junk_signed_token = "aGVsbG8=.deadbeef"

        server, port, _registry, _auth = await _start_bed_with_auth(
            secret=secrets.token_bytes(32),
        )
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws, {"type": "auth_refresh", "token": junk_signed_token}
                )
        finally:
            await server.stop()

        self.assertEqual(reply.get("type"), "error")
        self.assertEqual(reply.get("code"), "token_invalid")


class TestBedModeLoginSanity(unittest.IsolatedAsyncioTestCase):
    """Sanity checks that login / disconnect / list_services keep working."""

    async def test_login_bad_credentials_returns_bad_credentials(self):
        """Login with an unknown moniker returns
        ``code=bad_credentials``, NOT ``token_invalid`` (no token
        to validate yet) and NOT ``not_authenticated`` (login is
        the public entry point and does not require an existing
        session). This pins the wire contract so a future
        AuthService refactor that returns ``not_authenticated``
        on bad login trips the test.
        """
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "nobody", "password": "wrong"},
                )
        finally:
            await server.stop()

        self.assertEqual(reply.get("type"), "error")
        self.assertEqual(reply.get("code"), "bad_credentials")

    async def test_list_services_with_auth_only_returns_auth(self):
        """``list_services`` on a server with only AuthService
        registered returns ``{"type": "services",
        "services": ["auth", "auth_refresh", "auth_revoke",
        "reconnect"]}`` (sorted message-type keys). The actual
        wire shape comes from
        ``bbsengine6.net.transport.WebSocketServer._handle_list_services``
        (``bbsengine6/net/transport.py:751-769``): ``type`` is
        the literal string ``"services"`` and ``services`` is a
        sorted list of message-type strings, NOT a dict. A
        ``*default*`` slot is appended if any default was
        registered (none here).

        Pins the wire shape so the diagnostic helper documented
        in ``casino/docs/AUTH.md`` §7 can enumerate which
        handlers a given daemon has wired.
        """
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(ws, {"type": "list_services"})
        finally:
            await server.stop()

        self.assertEqual(reply.get("type"), "services")
        services = reply.get("services", [])
        self.assertIsInstance(services, list)
        # AuthService registers ``auth``, ``reconnect``,
        # ``auth_refresh``, ``auth_revoke`` — see
        # ``bed/api/auth.py:174`` ``HANDLED_TYPES``. The list is
        # sorted by ``_handle_list_services``.
        self.assertEqual(
            set(services),
            {"auth", "reconnect", "auth_refresh", "auth_revoke"},
        )
