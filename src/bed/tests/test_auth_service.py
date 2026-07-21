#!/usr/bin/env python3
# bed/tests/test_auth_service.py
# Unit tests for bed's AuthService (bearer tokens) and friends.

import argparse
import asyncio
import json
import os
import secrets
import stat
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/home/opencode/data/work/bed/src")
sys.path.insert(0, "/home/opencode/data/work/bbsengine6/py/src")


from bed.api import (
    AuthService,
    InMemoryTokenStore,
    InsecureSecretError,
    MemberInfo,
    MonikerOnlyCredentialProvider,
    PasswordCredentialProvider,
    SessionRegistry,
    TokenError,
    TokenRecord,
    error_envelope,
    get_provider,
    load_or_create_secret,
    scrub_token,
)
from bed.api.auth import _decode_token, _encode_token
from bed.api.errors import (
    CODE_BAD_CREDENTIALS,
    CODE_INSTANCE_MISMATCH,
    CODE_NOT_AUTHENTICATED,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    CODE_TOKEN_REVOKED,
)


class _FakeWebSocket:
    """Stand-in for a websockets.WebSocketClientProtocol.

    Provides an ``id`` attribute (a UUID) matching the interface of
    real websockets connections so auth handlers can use
    ``str(websocket.id)`` consistently.
    """

    def __init__(self) -> None:
        import uuid as _uuid
        self.id = _uuid.uuid4()
        self.sent: List[Dict[str, Any]] = []

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return self is other


def _fake_args() -> argparse.Namespace:
    return argparse.Namespace(debug=False, pool=None)


def _build_service(
    *,
    credential_provider: Any = None,
    token_store: Any = None,
    ttl_seconds: int = 900,
    instance_id: Optional[str] = None,
    clock: Optional[Any] = None,
) -> AuthService:
    creds = credential_provider if credential_provider is not None else _AlwaysAllowProvider()
    store = token_store if token_store is not None else InMemoryTokenStore()
    return AuthService(
        args=_fake_args(),
        session_registry=SessionRegistry(),
        token_store=store,
        credential_provider=creds,
        secret=secrets.token_bytes(32),
        instance_id=instance_id or "instance-A",
        ttl_seconds=ttl_seconds,
        clock=clock,
    )


class _AlwaysAllowProvider:
    def __init__(self, *, is_sysop: bool = False, balance: Optional[int] = 42) -> None:
        self.calls: List[tuple[str, str]] = []
        self.is_sysop = is_sysop
        self.balance = balance

    def authenticate(self, args: Any, moniker: str, password: str, *, pool: Any = None) -> Optional[MemberInfo]:
        self.calls.append((moniker, password))
        return MemberInfo(moniker=moniker, is_sysop=self.is_sysop, balance=self.balance)


class _AlwaysDenyProvider:
    def authenticate(self, args: Any, moniker: str, password: str, *, pool: Any = None) -> Optional[MemberInfo]:
        return None


class TestTokenCodec(unittest.TestCase):
    def test_roundtrip(self) -> None:
        secret = secrets.token_bytes(32)
        claims = {
            "moniker": "alice",
            "issued_at": 100.0,
            "expires_at": 100.0 + 900,
            "session_id": "s1",
            "is_sysop": False,
            "bed_instance_id": "instance-A",
            "websocket_id": "ws1",
        }
        token = _encode_token(claims, secret)
        decoded = _decode_token(token, secret)
        self.assertEqual(decoded["moniker"], "alice")
        self.assertEqual(decoded["session_id"], "s1")
        self.assertFalse(decoded["is_sysop"])

    def test_bad_signature_raises(self) -> None:
        secret = secrets.token_bytes(32)
        other = secrets.token_bytes(32)
        token = _encode_token({"moniker": "a"}, secret)
        with self.assertRaises(TokenError) as cm:
            _decode_token(token, other)
        self.assertEqual(cm.exception.code, CODE_TOKEN_INVALID)

    def test_malformed_raises(self) -> None:
        secret = secrets.token_bytes(32)
        for bad in ("", "no-dot", ".", "payload.", "payload.mac"):
            with self.assertRaises(TokenError):
                _decode_token(bad, secret)

    def test_tampered_payload_raises(self) -> None:
        secret = secrets.token_bytes(32)
        token = _encode_token({"moniker": "a"}, secret)
        payload_b64, mac = token.rsplit(".", 1)
        tampered = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B") + "." + mac
        with self.assertRaises(TokenError):
            _decode_token(tampered, secret)


class TestScrubToken(unittest.TestCase):
    def test_scrubs_nested_tokens(self) -> None:
        obj = {
            "token": "abc",
            "nested": {"token": "def", "other": "leave"},
            "list": [{"token": "ghi"}],
        }
        scrubbed = scrub_token(obj)
        self.assertEqual(scrubbed["token"], "<redacted>")
        self.assertEqual(scrubbed["nested"]["token"], "<redacted>")
        self.assertEqual(scrubbed["nested"]["other"], "leave")
        self.assertEqual(scrubbed["list"][0]["token"], "<redacted>")

    def test_no_match_passes_through(self) -> None:
        self.assertEqual(scrub_token("hello"), "hello")
        self.assertEqual(scrub_token(42), 42)


class TestErrorEnvelope(unittest.TestCase):
    def test_envelope_shape(self) -> None:
        env = error_envelope("c", "m", recoverable=True)
        self.assertEqual(env["type"], "error")
        self.assertEqual(env["code"], "c")
        self.assertEqual(env["message"], "m")
        self.assertTrue(env["recoverable"])


class TestAuthServiceIssue(unittest.IsolatedAsyncioTestCase):
    async def test_issue_and_validate_round_trip(self) -> None:
        provider = _AlwaysAllowProvider(is_sysop=True, balance=99)
        service = _build_service(credential_provider=provider)
        ws = _FakeWebSocket()
        resp = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "alice", "password": "pw"}
        )
        self.assertEqual(resp["type"], "auth_result")
        self.assertTrue(resp["success"])
        self.assertEqual(resp["moniker"], "alice")
        self.assertTrue(resp["is_sysop"])
        self.assertEqual(resp["balance"], 99)
        self.assertIn("token", resp)
        self.assertIn("session_id", resp)
        self.assertIn("expires_at", resp)

        record = service.token_store.get(resp["token"])
        self.assertIsNotNone(record)
        self.assertEqual(record.moniker, "alice")
        self.assertTrue(record.is_sysop)
        self.assertEqual(provider.calls, [("alice", "pw")])

    async def test_missing_credentials(self) -> None:
        service = _build_service()
        for payload in (
            {"type": "auth"},
            {"type": "auth", "moniker": "", "password": "x"},
            {"type": "auth", "moniker": "x", "password": ""},
        ):
            resp = await service.handle_message(None, _FakeWebSocket(), "/", payload)
            self.assertEqual(resp["type"], "error")
            self.assertFalse(resp["recoverable"])

    async def test_bad_credentials(self) -> None:
        service = _build_service(credential_provider=_AlwaysDenyProvider())
        resp = await service.handle_message(
            None,
            _FakeWebSocket(),
            "/",
            {"type": "auth", "moniker": "alice", "password": "wrong"},
        )
        self.assertEqual(resp["code"], CODE_BAD_CREDENTIALS)
        self.assertFalse(resp["recoverable"])


class TestAuthServiceExpiry(unittest.IsolatedAsyncioTestCase):
    async def test_expired_token_rejected_on_reconnect(self) -> None:
        clock = [0.0]
        store = InMemoryTokenStore(now_factory=lambda: clock[0])
        service = _build_service(token_store=store, clock=lambda: clock[0])
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        token = auth["token"]

        rec = store.get(token)
        self.assertIsNotNone(rec)
        service.sessions.get_by_session(rec.session_id).pending_request = None

        clock[0] = rec.expires_at + 1

        resp = await service.handle_message(
            None, _FakeWebSocket(), "/", {"type": "reconnect", "token": token}
        )
        self.assertIn(resp["code"], (CODE_TOKEN_EXPIRED, CODE_TOKEN_REVOKED))
        self.assertFalse(resp["recoverable"]) if resp["code"] == CODE_TOKEN_REVOKED else self.assertTrue(resp["recoverable"])
        self.assertIsNone(store.get(token))

    async def test_token_invalid_signature(self) -> None:
        service = _build_service()
        resp = await service.handle_message(
            None, _FakeWebSocket(), "/", {"type": "reconnect", "token": "abc.def"}
        )
        self.assertEqual(resp["code"], CODE_TOKEN_INVALID)
        self.assertFalse(resp["recoverable"])


class TestAuthServiceRefresh(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_rotates_token(self) -> None:
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        old_token = auth["token"]

        refresh = await service.handle_message(
            None, ws, "/", {"type": "auth_refresh", "token": old_token}
        )
        self.assertEqual(refresh["type"], "auth_result")
        self.assertTrue(refresh["success"])
        new_token = refresh["token"]
        self.assertNotEqual(new_token, old_token)
        self.assertIsNone(store.get(old_token))
        self.assertIsNotNone(store.get(new_token))

    async def test_refresh_without_live_socket_rejected(self) -> None:
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        other_ws = _FakeWebSocket()
        resp = await service.handle_message(
            other_ws,
            None,
            "/",
            {"type": "auth_refresh", "token": auth["token"]},
        )
        self.assertEqual(resp["code"], CODE_NOT_AUTHENTICATED)


class TestAuthServiceReconnect(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_rebinds_websocket_and_replays_pending(self) -> None:
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws1 = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws1, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        token = auth["token"]
        record = store.get(token)
        self.assertIsNotNone(record)

        pending = {"type": "inputstring", "request_id": "r1", "prompt": "?"}
        service.sessions.record_pending(record.session_id, pending)

        ws2 = _FakeWebSocket()
        resp = await service.handle_message(
            None, ws2, "/", {"type": "reconnect", "token": token}
        )
        self.assertEqual(resp["type"], "reconnect_result")
        self.assertTrue(resp["success"])
        self.assertEqual(resp["replayed"], pending)
        self.assertEqual(resp["replayed_request_id"], "r1")
        self.assertIsNone(store.get(token))
        new_record = store.get(resp["token"])
        self.assertIsNotNone(new_record)

        state = service.sessions.get_by_websocket(str(ws2.id))
        self.assertIsNotNone(state)
        self.assertEqual(state.session_id, record.session_id)
        self.assertIsNone(state.pending_request)

    async def test_reconnect_revoked_token_rejected(self) -> None:
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        token = auth["token"]
        record = store.get(token)
        self.assertIsNotNone(record)
        service.sessions.get_by_session(record.session_id)
        store.delete(token)

        resp = await service.handle_message(
            None, _FakeWebSocket(), "/", {"type": "reconnect", "token": token}
        )
        self.assertEqual(resp["code"], CODE_TOKEN_REVOKED)

    async def test_cross_instance_rejected(self) -> None:
        store = InMemoryTokenStore()
        svc_a = _build_service(token_store=store, instance_id="instance-A")
        ws_a = _FakeWebSocket()
        auth = await svc_a.handle_message(
            None, ws_a, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        token = auth["token"]

        svc_b = _build_service(token_store=store, instance_id="instance-B")
        resp = await svc_b.handle_message(
            None, _FakeWebSocket(), "/", {"type": "reconnect", "token": token}
        )
        self.assertEqual(resp["code"], CODE_TOKEN_INVALID)
        self.assertFalse(resp["recoverable"])

    async def test_instance_mismatch_via_same_secret(self) -> None:
        secret = secrets.token_bytes(32)
        store = InMemoryTokenStore()
        svc_a = AuthService(
            args=_fake_args(),
            session_registry=SessionRegistry(),
            token_store=store,
            credential_provider=_AlwaysAllowProvider(),
            secret=secret,
            instance_id="instance-A",
            ttl_seconds=900,
        )
        auth = await svc_a.handle_message(
            None, _FakeWebSocket(), "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        token = auth["token"]

        svc_b = AuthService(
            args=_fake_args(),
            session_registry=SessionRegistry(),
            token_store=store,
            credential_provider=_AlwaysAllowProvider(),
            secret=secret,
            instance_id="instance-B",
            ttl_seconds=900,
        )
        resp = await svc_b.handle_message(
            None, _FakeWebSocket(), "/", {"type": "reconnect", "token": token}
        )
        self.assertEqual(resp["code"], CODE_INSTANCE_MISMATCH)
        self.assertIsNone(store.get(token))


class TestAuthServiceRevoke(unittest.IsolatedAsyncioTestCase):
    async def test_revoke_invalidates_token(self) -> None:
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        token = auth["token"]

        resp = await service.handle_message(
            None, ws, "/", {"type": "auth_revoke", "token": token}
        )
        self.assertEqual(resp["type"], "auth_revoke_result")
        self.assertTrue(resp["success"])
        self.assertIsNone(store.get(token))

    async def test_revoke_invalid_signature(self) -> None:
        service = _build_service()
        resp = await service.handle_message(
            None, _FakeWebSocket(), "/", {"type": "auth_revoke", "token": "abc.def"}
        )
        self.assertEqual(resp["type"], "auth_revoke_result")
        self.assertFalse(resp["success"])
        self.assertEqual(resp["code"], CODE_TOKEN_INVALID)


class TestTokenScrubbingInLogs(unittest.IsolatedAsyncioTestCase):
    async def test_token_does_not_leak_to_debug_log(self) -> None:
        captured: List[str] = []
        from bed.api import auth as auth_mod

        original = auth_mod.io.echo

        def _capture(msg, level="info"):
            captured.append(str(msg))
            return original(msg, level=level)

        auth_mod.io.echo = _capture
        try:
            service = _build_service()
            ws = _FakeWebSocket()
            auth = await service.handle_message(
                None, ws, "/", {"type": "auth", "moniker": "leaky", "password": "p"}
            )
            token = auth["token"]
            for line in captured:
                self.assertNotIn(token, line, f"token leaked into log: {line!r}")
        finally:
            auth_mod.io.echo = original


class TestInMemoryTokenStore(unittest.TestCase):
    def test_gc_expired_returns_count(self) -> None:
        store = InMemoryTokenStore()
        rec = TokenRecord(
            token="t",
            moniker="m",
            session_id="s",
            issued_at=0.0,
            expires_at=0.0,
            is_sysop=False,
            bed_instance_id="i",
            websocket_id="w",
        )
        store.put(rec)
        self.assertEqual(store.gc_expired(now=1.0), 1)
        self.assertIsNone(store.get("t"))

    def test_get_returns_none_for_missing(self) -> None:
        store = InMemoryTokenStore()
        self.assertIsNone(store.get("nope"))


class TestSecretLoader(unittest.TestCase):
    def test_load_or_create_secret_creates_0600(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.secret")
            secret, instance_id = load_or_create_secret(path)
            self.assertEqual(len(secret), 32)
            self.assertTrue(instance_id)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)

    def test_load_or_create_secret_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.secret")
            s1, i1 = load_or_create_secret(path)
            s2, i2 = load_or_create_secret(path)
            self.assertEqual(s1, s2)
            self.assertEqual(i1, i2)

    def test_refuses_world_readable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.secret")
            secret_bytes, instance_id = load_or_create_secret(path)
            payload_bytes = json.dumps(
                {
                    "__bed_secret_version": 2,
                    "hmac": secret_bytes.hex(),
                    "instance_id": instance_id,
                }
            ).encode()
            with open(path, "wb") as f:
                f.write(payload_bytes)
            os.chmod(path, 0o644)
            with self.assertRaises(InsecureSecretError):
                load_or_create_secret(path)
            os.chmod(path, 0o600)

    def test_explicit_instance_id_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.secret")
            s1, _i1 = load_or_create_secret(path)
            s2, i2 = load_or_create_secret(path, explicit_instance_id="hard-coded-id")
            self.assertEqual(s1, s2)
            self.assertEqual(i2, "hard-coded-id")


class TestProviderFactory(unittest.TestCase):
    def test_password_default(self) -> None:
        p = get_provider("password")
        self.assertIsInstance(p, PasswordCredentialProvider)

    def test_moniker_only(self) -> None:
        p = get_provider("moniker-only")
        self.assertIsInstance(p, MonikerOnlyCredentialProvider)

    def test_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_provider("nope")


class TestSessionRegistry(unittest.TestCase):
    def test_bind_and_lookup(self) -> None:
        r = SessionRegistry()
        r.bind("s1", "w1", "alice", False, balance=10)
        self.assertEqual(r.get_by_session("s1").moniker, "alice")
        self.assertEqual(r.get_by_websocket("w1").session_id, "s1")
        self.assertEqual(r.get_by_session("s1").balance, 10)

    def test_rebind_websocket(self) -> None:
        r = SessionRegistry()
        r.bind("s1", "w1", "alice", False)
        st = r.rebind_websocket("w1", "w2")
        self.assertIsNotNone(st)
        self.assertIsNone(r.get_by_websocket("w1"))
        self.assertEqual(r.get_by_websocket("w2").session_id, "s1")

    def test_request_id_monotonic(self) -> None:
        r = SessionRegistry()
        r.bind("s1", "w1", "a", False)
        r1 = r.next_request_id("s1")
        r2 = r.next_request_id("s1")
        self.assertEqual(r1, "r1")
        self.assertEqual(r2, "r2")

    def test_pending_record_take_clear(self) -> None:
        r = SessionRegistry()
        r.bind("s1", "w1", "a", False)
        r.record_pending("s1", {"type": "x"})
        self.assertEqual(r.take_pending("s1"), {"type": "x"})
        self.assertIsNone(r.take_pending("s1"))
        r.record_pending("s1", {"type": "y"})
        r.clear_pending("s1")
        self.assertIsNone(r.take_pending("s1"))


class TestBEDWiring(unittest.TestCase):
    """BED main flow: AuthService is wired for non-DefaultRouter + DB JSON flags."""

    def _bed_args(self, **overrides: Any) -> argparse.Namespace:
        defaults = dict(
            host="127.0.0.1",
            port=0,
            debug=False,
            databasename="x",
            databasehost="x",
            databaseport=0,
            databaseuser="x",
            databasepassword="x",
            bed_secret="",
            token_ttl=900,
            token_persistence="memory",
            credential_provider="password",
            bed_instance_id=None,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_is_default_router_detection(self) -> None:
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        bed = BED(self._bed_args(), DefaultRouter)
        self.assertTrue(bed._is_default_router())
        self.assertFalse(bed._auth_enabled())

        class _Other:
            pass

        bed2 = BED(self._bed_args(), _Other)
        self.assertFalse(bed2._is_default_router())

    def test_auth_disabled_when_token_persistence_none(self) -> None:
        from bbsengine6.net.defaultrouter import DefaultRouter
        from bed.main import BED

        bed = BED(self._bed_args(token_persistence="none"), DefaultRouter)
        self.assertFalse(bed._auth_enabled())

    def test_auth_config_apply(self) -> None:
        from bed.main import _apply_auth_config, _get_bed_defaults

        defaults = _get_bed_defaults()
        args = self._bed_args(
            bed_secret=defaults["bed_secret"],
            token_ttl=defaults["token_ttl"],
            token_persistence=defaults["token_persistence"],
            credential_provider=defaults["credential_provider"],
        )
        cfg = {
            "auth": {
                "bed_secret_path": "/tmp/custom.secret",
                "token_ttl": 600,
                "token_persistence": "db",
                "credential_provider": "moniker-only",
                "bed_instance_id": "fixed-id",
            }
        }
        _apply_auth_config(args, cfg)
        self.assertEqual(args.bed_secret, "/tmp/custom.secret")
        self.assertEqual(args.token_ttl, 600)
        self.assertEqual(args.token_persistence, "db")
        self.assertEqual(args.credential_provider, "moniker-only")
        self.assertEqual(args.bed_instance_id, "fixed-id")

        args2 = self._bed_args(token_ttl=42)
        _apply_auth_config(args2, cfg)
        self.assertEqual(args2.token_ttl, 42)

    def test_insecure_secret_refuses_to_load(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bed.secret")
            with open(bad, "w") as f:
                f.write("not-a-secret")
            os.chmod(bad, 0o644)
            try:
                with self.assertRaises(InsecureSecretError):
                    load_or_create_secret(bad)
            finally:
                os.chmod(bad, 0o600)


class TestAuthEndToEndOverWebSocket(unittest.IsolatedAsyncioTestCase):
    """Full auth -> reconnect -> refresh -> revoke over a real WebSocket.

    Uses a mock-pool-stamped BED wired with AuthService + a no-op router
    to exercise the live JSON dispatch path.
    """

    async def asyncSetUp(self) -> None:
        import socket
        from bbsengine6.net import WebSocketServer
        from bed.api import AuthService, InMemoryTokenStore, SessionRegistry

        class _Provider:
            def authenticate(self, args, moniker, password, *, pool=None):
                from bed.api import MemberInfo
                if moniker == "alice" and password == "pw":
                    return MemberInfo(moniker="alice", is_sysop=False, balance=7)
                return None

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]

        self.token_store = InMemoryTokenStore()
        self.registry = SessionRegistry()
        provider = _Provider()
        args = argparse.Namespace(debug=False, pool=None)
        self.auth_service = AuthService(
            args=args,
            session_registry=self.registry,
            token_store=self.token_store,
            credential_provider=provider,
            secret=secrets.token_bytes(32),
            instance_id="e2e-instance",
            ttl_seconds=900,
        )
        self.server = WebSocketServer(host="127.0.0.1", port=self.port)
        self.auth_service.register_all(self.server)
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def _send_recv(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import websockets

        uri = f"ws://127.0.0.1:{self.port}/"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(payload))
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            return json.loads(raw)

    async def test_full_lifecycle(self) -> None:
        import websockets

        uri = f"ws://127.0.0.1:{self.port}/"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "auth", "moniker": "alice", "password": "pw"}))
            auth = json.loads(await ws.recv())
            self.assertEqual(auth["type"], "auth_result")
            self.assertTrue(auth["success"])
            self.assertEqual(auth["moniker"], "alice")
            self.assertEqual(auth["balance"], 7)
            token1 = auth["token"]

            await ws.send(json.dumps({"type": "auth_refresh", "token": token1}))
            refresh = json.loads(await ws.recv())
            self.assertEqual(refresh["type"], "auth_result")
            self.assertTrue(refresh["success"])
            token2 = refresh["token"]
            self.assertNotEqual(token1, token2)
            self.assertIsNone(self.token_store.get(token1))
            self.assertIsNotNone(self.token_store.get(token2))

        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps({"type": "reconnect", "token": token2}))
            reconnect = json.loads(await ws2.recv())
            self.assertEqual(reconnect["type"], "reconnect_result")
            self.assertTrue(reconnect["success"])
            token3 = reconnect["token"]
            self.assertIsNone(self.token_store.get(token2))
            self.assertIsNotNone(self.token_store.get(token3))

        async with websockets.connect(uri) as ws3:
            await ws3.send(json.dumps({"type": "auth_revoke", "token": token3}))
            revoke = json.loads(await ws3.recv())
            self.assertEqual(revoke["type"], "auth_revoke_result")
            self.assertTrue(revoke["success"])
            self.assertIsNone(self.token_store.get(token3))

    async def test_bad_password(self) -> None:
        bad = await self._send_recv(
            {"type": "auth", "moniker": "alice", "password": "wrong"}
        )
        self.assertEqual(bad["type"], "error")
        self.assertEqual(bad["code"], CODE_BAD_CREDENTIALS)


if __name__ == "__main__":
    unittest.main()
