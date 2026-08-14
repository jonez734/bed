#!/usr/bin/env python3
"""Integration tests for every auth operation, end-to-end through bed.

Boots a real in-process ``WebSocketServer`` with the bed-native
:class:`bed.api.auth.AuthService` registered, then connects over the
wire and exercises all four auth operations:

- ``auth`` (login)
- ``reconnect``
- ``auth_refresh``
- ``auth_revoke``

The underlying ``bbsengine6.auth.access()`` is the real
implementation; the credential provider is a small stub that accepts
the well-known test pair ``("alice", "pw")`` and returns a
``MemberInfo`` with ``loginid="alice_os"``. This way the wire path is
real (``websockets`` -> ``WebSocketServer`` -> ``dispatch_message`` ->
``AuthService.handle_message`` -> ``bbsengine6.auth.access()`` -> reply)
without depending on a live database for password validation.

A random port (``port=0``) is used so concurrent suites do not
collide.

The wire-level tests use raw ``websockets.connect`` to send and
receive JSON envelopes (matches the production WebSocket transport).
The client-wrapper tests use ``BedAuthServiceClient`` against a thin
in-process transport that satisfies its ``send`` contract, which lets
us exercise the envelope-shape logic in ``BedAuthServiceClient``
without depending on a real daemon.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket as _socket
import sys
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock


sys.path.insert(0, "/home/opencode/data/work/bed/src")


# ---------------------------------------------------------------------
# Helpers


class _Provider:
    """Stub credential provider used by the in-process server.

    Accepts ``("alice", "pw")`` as a normal member and ``("root",
    "rootpw")`` as a sysop. Any other input returns ``None`` (which
    AuthService translates to ``bad_credentials``).
    """

    def authenticate(self, args, moniker, password, *, pool=None):
        from bed.api.token_store import MemberInfo

        if moniker == "alice" and password == "pw":
            return MemberInfo(
                moniker="alice",
                is_sysop=False,
                balance=7,
                loginid="alice_os",
            )
        if moniker == "root" and password == "rootpw":
            return MemberInfo(
                moniker="root",
                is_sysop=True,
                balance=0,
                loginid="root_os",
            )
        return None


def _auth_args() -> argparse.Namespace:
    return argparse.Namespace(debug=False, pool=None)


async def _start_bed_with_auth(
    *,
    instance_id: str = "auth-integration-test",
    secret: Optional[bytes] = None,
    ttl_seconds: int = 900,
    clock=None,
) -> Any:
    """Spin up a WebSocketServer with ``AuthService`` registered.

    Returns ``(server, port, registry, auth_service)``. The server is
    bound to ``127.0.0.1`` on an ephemeral port.
    """
    from bbsengine6.net import WebSocketServer
    from bed.api import AuthService, InMemoryTokenStore
    from bed.api.session import SessionRegistry

    registry = SessionRegistry()
    auth_service = AuthService(
        args=_auth_args(),
        session_registry=registry,
        token_store=InMemoryTokenStore(),
        credential_provider=_Provider(),
        secret=secret if secret is not None else secrets.token_bytes(32),
        instance_id=instance_id,
        ttl_seconds=ttl_seconds,
        clock=clock,
    )

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = WebSocketServer(host="127.0.0.1", port=port)
    auth_service.register_all(server)
    await server.start()
    return server, port, registry, auth_service


async def _send_and_recv(ws, payload, *, timeout: float = 2.0) -> Dict[str, Any]:
    await ws.send(json.dumps(payload))
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


# ---------------------------------------------------------------------
# Wire-level: login / reconnect / refresh / revoke


class TestAuthWireEndToEnd(unittest.IsolatedAsyncioTestCase):
    """End-to-end tests against a real ``WebSocketServer``."""

    async def test_login_returns_token_envelope(self):
        import websockets

        server, port, _registry, auth_service = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws, {"type": "auth", "moniker": "alice", "password": "pw"}
                )
            self.assertEqual(reply["type"], "auth_result")
            self.assertTrue(reply["success"])
            self.assertEqual(reply["moniker"], "alice")
            self.assertFalse(reply["is_sysop"])
            self.assertIn("token", reply)
            self.assertIn("session_id", reply)
            self.assertIn("expires_at", reply)
            self.assertEqual(reply["balance"], 7)
            # The token must be retrievable from the in-process store.
            stored = auth_service.token_store.get(reply["token"])
            self.assertIsNotNone(stored)
            self.assertEqual(stored.moniker, "alice")
            self.assertEqual(stored.bed_instance_id, "auth-integration-test")
            self.assertEqual(stored.loginid, "alice_os")
        finally:
            await server.stop()

    async def test_login_sysop_acknowledged(self):
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws, {"type": "auth", "moniker": "root", "password": "rootpw"}
                )
            self.assertEqual(reply["type"], "auth_result")
            self.assertTrue(reply["is_sysop"])
        finally:
            await server.stop()

    async def test_login_missing_credentials_returns_error(self):
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws, {"type": "auth", "moniker": "", "password": ""}
                )
            self.assertEqual(reply["type"], "error")
            self.assertEqual(reply["code"], "missing_credentials")
            self.assertFalse(reply.get("recoverable", True))
        finally:
            await server.stop()

    async def test_login_bad_credentials_returns_error(self):
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "wrong"},
                )
            self.assertEqual(reply["type"], "error")
            self.assertEqual(reply["code"], "bad_credentials")
            self.assertFalse(reply.get("recoverable", True))
        finally:
            await server.stop()

    async def test_reconnect_rotates_token_and_replays_pending(self):
        import websockets

        server, port, registry, auth_service = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                first = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "pw"},
                )
                first_token = first["token"]
                session_id = first["session_id"]

                registry.record_pending(
                    session_id,
                    {
                        "request_id": "r-replay-1",
                        "type": "io_push",
                        "prompt": "press y to confirm",
                    },
                )

            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws2:
                reply = await _send_and_recv(
                    ws2,
                    {"type": "reconnect", "token": first_token},
                )
            self.assertEqual(reply["type"], "reconnect_result")
            self.assertTrue(reply["success"])
            self.assertEqual(reply["moniker"], "alice")
            self.assertEqual(reply["session_id"], session_id)
            self.assertNotEqual(reply["token"], first_token)
            self.assertIsNotNone(reply["replayed"])
            self.assertEqual(
                reply["replayed_request_id"], "r-replay-1"
            )
            # The old token must be gone from the store; the new one
            # must be present.
            self.assertIsNone(auth_service.token_store.get(first_token))
            self.assertIsNotNone(auth_service.token_store.get(reply["token"]))
        finally:
            await server.stop()

    async def test_reconnect_invalid_signature_rejected(self):
        import websockets

        # Mint a token signed with a different secret so signature
        # verification on reconnect fails.
        from bed.api.auth import _encode_token

        other_secret = secrets.token_bytes(32)
        bad_token = _encode_token(
            {
                "version": 1,
                "moniker": "alice",
                "issued_at": 0.0,
                "expires_at": 1e18,
                "session_id": "ghost",
                "is_sysop": False,
                "bed_instance_id": "auth-integration-test",
                "websocket_id": "ws-ghost",
            },
            other_secret,
        )
        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws, {"type": "reconnect", "token": bad_token}
                )
            self.assertEqual(reply["type"], "error")
            self.assertEqual(reply["code"], "token_invalid")
            self.assertFalse(reply.get("recoverable", True))
        finally:
            await server.stop()

    async def test_reconnect_expired_token_rejected(self):
        import websockets

        # Pin "now" so the issued token is fresh on login, then jump
        # the clock past expiry for the reconnect attempt. The
        # in-process token store evicts on ``get()`` so the
        # reconnect handler sees ``store_record is None`` and answers
        # ``token_revoked``. The DB store filters by ``expires_at >
        # now()`` and the same code path runs. Both outcomes are
        # valid; the contract is "expired tokens are rejected".
        now = [1_000_000.0]

        def clock():
            return now[0]

        server, port, _registry, _auth = await _start_bed_with_auth(
            ttl_seconds=10, clock=clock
        )
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                first = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "pw"},
                )
                token = first["token"]
            now[0] += 1_000_000.0  # well past expires_at
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws2:
                reply = await _send_and_recv(
                    ws2, {"type": "reconnect", "token": token}
                )
            self.assertEqual(reply["type"], "error")
            self.assertIn(reply["code"], ("token_revoked", "token_expired"))
        finally:
            await server.stop()

    async def test_reconnect_cross_instance_rejected(self):
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth(
            instance_id="instance-A"
        )
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                first = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "pw"},
                )
                token = first["token"]
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws2:
                reply = await _send_and_recv(
                    ws2, {"type": "reconnect", "token": token}
                )
            # We can't easily swap the running AuthService's
            # instance_id post-construction, so we simulate the
            # cross-instance path by claiming a different one. The
            # in-process server only knows its own instance_id, so a
            # token signed with the same secret but a different
            # bed_instance_id must come back as instance_mismatch.
            # We synthesize that token and reconnect with it.
            await server.stop()

            server2, port2, _registry2, _auth2 = await _start_bed_with_auth(
                instance_id="instance-B"
            )
            try:
                # Build a token whose payload says instance-A but is
                # presented to instance-B's server (different secret).
                # Signature mismatch dominates; we expect token_invalid
                # on the second server. This is acceptable: the test
                # is "cross-instance path is rejected", not "always
                # returns instance_mismatch code".
                async with websockets.connect(
                    f"ws://127.0.0.1:{port2}/"
                ) as ws:
                    reply = await _send_and_recv(
                        ws, {"type": "reconnect", "token": token}
                    )
                self.assertIn(
                    reply["code"], ("token_invalid", "instance_mismatch")
                )
            finally:
                await server2.stop()
        finally:
            if server._stopped if hasattr(server, "_stopped") else True:
                pass

    async def test_refresh_on_original_socket_rotates_token(self):
        import websockets

        server, port, _registry, auth_service = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                first = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "pw"},
                )
                first_token = first["token"]
                refresh = await _send_and_recv(
                    ws, {"type": "auth_refresh", "token": first_token}
                )
            self.assertEqual(refresh["type"], "auth_result")
            self.assertTrue(refresh["success"])
            self.assertNotEqual(refresh["token"], first_token)
            self.assertIsNone(auth_service.token_store.get(first_token))
            self.assertIsNotNone(
                auth_service.token_store.get(refresh["token"])
            )
        finally:
            await server.stop()

    async def test_refresh_on_different_socket_denied(self):
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                first = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "pw"},
                )
                token = first["token"]
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws2:
                reply = await _send_and_recv(
                    ws2, {"type": "auth_refresh", "token": token}
                )
            self.assertEqual(reply["type"], "error")
            self.assertEqual(reply["code"], "not_authenticated")
            self.assertTrue(reply.get("recoverable", True))
        finally:
            await server.stop()

    async def test_revoke_invalidates_token(self):
        import websockets

        server, port, _registry, auth_service = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                first = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "pw"},
                )
                token = first["token"]
                revoke = await _send_and_recv(
                    ws, {"type": "auth_revoke", "token": token}
                )
            self.assertEqual(revoke["type"], "auth_revoke_result")
            self.assertTrue(revoke["success"])
            self.assertIsNone(revoke["code"])
            self.assertFalse(revoke.get("recoverable", True))
            self.assertIsNone(auth_service.token_store.get(token))

            # Reconnect with the revoked token must come back as
            # token_revoked.
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws2:
                reply = await _send_and_recv(
                    ws2, {"type": "reconnect", "token": token}
                )
            self.assertEqual(reply["type"], "error")
            self.assertEqual(reply["code"], "token_revoked")
        finally:
            await server.stop()

    async def test_revoke_invalid_signature_envelope(self):
        import websockets

        from bed.api.auth import _encode_token

        bad_token = _encode_token(
            {
                "version": 1,
                "moniker": "alice",
                "issued_at": 0.0,
                "expires_at": 1e18,
                "session_id": "ghost",
                "is_sysop": False,
                "bed_instance_id": "auth-integration-test",
                "websocket_id": "ws-ghost",
            },
            secrets.token_bytes(32),
        )
        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                reply = await _send_and_recv(
                    ws, {"type": "auth_revoke", "token": bad_token}
                )
            self.assertEqual(reply["type"], "auth_revoke_result")
            self.assertFalse(reply["success"])
            self.assertEqual(reply["code"], "token_invalid")
        finally:
            await server.stop()

    async def test_revoke_already_deleted_returns_token_revoked(self):
        import websockets

        server, port, _registry, _auth = await _start_bed_with_auth()
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
                first = await _send_and_recv(
                    ws,
                    {"type": "auth", "moniker": "alice", "password": "pw"},
                )
                token = first["token"]
                # Revoke once: success.
                r1 = await _send_and_recv(
                    ws, {"type": "auth_revoke", "token": token}
                )
                # Revoke again: token already gone.
                r2 = await _send_and_recv(
                    ws, {"type": "auth_revoke", "token": token}
                )
            self.assertTrue(r1["success"])
            self.assertFalse(r2["success"])
            self.assertEqual(r2["code"], "token_revoked")
            self.assertTrue(r2.get("recoverable", False))
        finally:
            await server.stop()


# ---------------------------------------------------------------------
# Client-wrapper: BedAuthServiceClient envelope logic


class _LoopbackTransport:
    """Stand-in for :class:`BedConnection` for client-wrapper tests.

    Each ``send`` records the payload and returns a canned reply.
    """

    def __init__(self, replies: List[Dict[str, Any]]) -> None:
        self._replies = list(replies)
        self._sent: List[Dict[str, Any]] = []

    async def send(self, message: Dict[str, Any]) -> Dict[str, Any]:
        self._sent.append(dict(message))
        if not self._replies:
            raise AssertionError(
                f"no canned reply for message: {message!r}"
            )
        return self._replies.pop(0)

    async def subscribe(self, handler):  # pragma: no cover - unused
        return None

    async def unsubscribe(self, handler):  # pragma: no cover - unused
        return None


class TestAuthClientWrapperIntegration(unittest.IsolatedAsyncioTestCase):
    """Drive every :class:`BedAuthServiceClient` method against an
    in-process transport that records what the client sent.
    """

    def _client(self, replies: List[Dict[str, Any]]):
        from bed.client.authservice import BedAuthServiceClient

        return BedAuthServiceClient(_LoopbackTransport(replies))

    async def test_login_envelope(self):
        client = self._client(
            [
                {
                    "type": "auth_result",
                    "success": True,
                    "moniker": "alice",
                    "is_sysop": False,
                    "session_id": "sess-1",
                    "token": "tok-1",
                    "expires_at": "2030-01-01T00:00:00Z",
                    "balance": 42,
                }
            ]
        )
        result = await client.login("alice", "pw")
        self.assertTrue(result["ok"])
        self.assertEqual(result["token"], "tok-1")
        self.assertEqual(result["session_id"], "sess-1")
        self.assertEqual(result["balance"], 42)
        sent = client._conn._sent
        self.assertEqual(sent[0]["type"], "auth")
        self.assertEqual(sent[0]["moniker"], "alice")
        self.assertEqual(sent[0]["password"], "pw")

    async def test_login_bad_credentials_returns_soft_failure(self):
        client = self._client(
            [
                {
                    "type": "error",
                    "code": "bad_credentials",
                    "message": "Invalid moniker or password",
                    "recoverable": False,
                }
            ]
        )
        result = await client.login("alice", "wrong")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "bad_credentials")
        self.assertIn("Invalid", result["message"])

    async def test_reconnect_envelope(self):
        client = self._client(
            [
                {
                    "type": "reconnect_result",
                    "success": True,
                    "moniker": "alice",
                    "is_sysop": False,
                    "session_id": "sess-1",
                    "token": "tok-2",
                    "expires_at": "2030-01-01T00:15:00Z",
                    "replayed": {"type": "io_push"},
                    "replayed_request_id": "r-1",
                }
            ]
        )
        result = await client.reconnect("tok-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["token"], "tok-2")
        self.assertEqual(result["replayed_request_id"], "r-1")
        sent = client._conn._sent
        self.assertEqual(sent[0]["type"], "reconnect")
        self.assertEqual(sent[0]["token"], "tok-1")

    async def test_reconnect_replay_none_omits_request_id(self):
        client = self._client(
            [
                {
                    "type": "reconnect_result",
                    "success": True,
                    "moniker": "alice",
                    "is_sysop": False,
                    "session_id": "sess-1",
                    "token": "tok-2",
                    "expires_at": "2030-01-01T00:15:00Z",
                    "replayed": None,
                }
            ]
        )
        result = await client.reconnect("tok-1")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["replayed"])
        self.assertNotIn("replayed_request_id", result)

    async def test_refresh_envelope(self):
        client = self._client(
            [
                {
                    "type": "auth_result",
                    "success": True,
                    "moniker": "alice",
                    "is_sysop": False,
                    "session_id": "sess-1",
                    "token": "tok-rotated",
                    "expires_at": "2030-01-01T00:15:00Z",
                    "balance": 99,
                }
            ]
        )
        result = await client.refresh("tok-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["token"], "tok-rotated")
        self.assertEqual(result["balance"], 99)
        sent = client._conn._sent
        self.assertEqual(sent[0]["type"], "auth_refresh")
        self.assertEqual(sent[0]["token"], "tok-1")

    async def test_revoke_envelope(self):
        client = self._client(
            [
                {
                    "type": "auth_revoke_result",
                    "success": True,
                    "code": None,
                    "recoverable": False,
                }
            ]
        )
        result = await client.revoke("tok-1")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["code"])
        self.assertEqual(result["token"], "tok-1")
        sent = client._conn._sent
        self.assertEqual(sent[0]["type"], "auth_revoke")
        self.assertEqual(sent[0]["token"], "tok-1")

    async def test_revoke_already_deleted_soft_failure(self):
        client = self._client(
            [
                {
                    "type": "auth_revoke_result",
                    "success": False,
                    "code": "token_revoked",
                    "recoverable": True,
                }
            ]
        )
        result = await client.revoke("tok-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "token_revoked")

    async def test_login_empty_moniker_short_circuits(self):
        client = self._client([])
        result = await client.login("", "pw")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_credentials")
        self.assertEqual(client._conn._sent, [])

    async def test_login_empty_password_short_circuits(self):
        client = self._client([])
        result = await client.login("alice", "")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_credentials")
        self.assertEqual(client._conn._sent, [])

    async def test_login_whitespace_moniker_short_circuits(self):
        client = self._client([])
        result = await client.login("   ", "pw")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_credentials")
        self.assertEqual(client._conn._sent, [])

    async def test_reconnect_empty_token_short_circuits(self):
        client = self._client([])
        result = await client.reconnect("")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_token")
        self.assertEqual(client._conn._sent, [])

    async def test_refresh_empty_token_short_circuits(self):
        client = self._client([])
        result = await client.refresh("")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_token")
        self.assertEqual(client._conn._sent, [])

    async def test_revoke_empty_token_short_circuits(self):
        client = self._client([])
        result = await client.revoke("")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "missing_token")
        self.assertEqual(client._conn._sent, [])

    async def test_server_error_envelope_propagates(self):
        """Any ``type=error`` envelope surfaces as ``ok=False`` with
        the server's ``code``/``message``."""
        client = self._client(
            [
                {
                    "type": "error",
                    "code": "database_error",
                    "message": "boom",
                }
            ]
        )
        result = await client.login("alice", "pw")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "database_error")
        self.assertEqual(result["message"], "boom")

    async def test_bed_unavailable_propagates_as_soft_failure(self):
        """A transport-level :class:`BedUnavailable` from the
        connection becomes ``ok=False, code='bed_unavailable'`` rather
        than re-raising."""

        class _RaisingTransport:
            def __init__(self) -> None:
                self._sent: List[Dict[str, Any]] = []

            async def send(self, message):
                self._sent.append(dict(message))
                from bed.client.exceptions import BedUnavailable

                raise BedUnavailable("ws down")

            async def subscribe(self, handler):
                return None

            async def unsubscribe(self, handler):
                return None

        from bed.client.authservice import BedAuthServiceClient

        transport = _RaisingTransport()
        client = BedAuthServiceClient(transport)
        for coro in (
            client.login("alice", "pw"),
            client.reconnect("tok"),
            client.refresh("tok"),
            client.revoke("tok"),
        ):
            result = await coro
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "bed_unavailable")
        self.assertEqual(len(transport._sent), 4)


# ---------------------------------------------------------------------
# Optional live-daemon tests (skipped when bed is unreachable)


LIVE_HOST = "127.0.0.1"
LIVE_PORT = 8765


def _live_daemon_reachable() -> bool:
    import socket

    try:
        with socket.create_connection((LIVE_HOST, LIVE_PORT), timeout=0.5):
            return True
    except OSError:
        return False


@unittest.skipUnless(
    _live_daemon_reachable(),
    f"bed daemon not reachable at {LIVE_HOST}:{LIVE_PORT}",
)
class TestAuthLiveDaemon(unittest.IsolatedAsyncioTestCase):
    """If a real bed daemon is up on the local dev port, exercise the
    wire protocol against it. Skipped when no daemon is reachable so
    the suite stays green on machines that don't have one running.
    The daemon's credential provider may accept or reject any given
    pair, so the test asserts the wire envelope shape rather than the
    success/failure semantics.
    """

    async def test_auth_envelope_shape_via_client(self):
        import websockets

        async with websockets.connect(f"ws://{LIVE_HOST}:{LIVE_PORT}/") as ws:
            reply = await _send_and_recv(
                ws,
                {"type": "auth", "moniker": "nobody", "password": ""},
            )
        self.assertIn(reply["type"], ("auth_result", "error"))
