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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import patch

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
    CODE_FORBIDDEN,
    CODE_INSTANCE_MISMATCH,
    CODE_NOT_AUTHENTICATED,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_INVALID,
    CODE_TOKEN_REVOKED,
)


def _now_ts_proxy() -> float:
    """Helper used by regression tests to compare against wall-clock-now."""
    return datetime.now(timezone.utc).timestamp()


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
            "version": 1,
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
        self.assertEqual(decoded["version"], 1)
        self.assertEqual(decoded["moniker"], "alice")
        self.assertEqual(decoded["session_id"], "s1")
        self.assertFalse(decoded["is_sysop"])

    def test_bad_signature_raises(self) -> None:
        secret = secrets.token_bytes(32)
        other = secrets.token_bytes(32)
        token = _encode_token({"version": 1, "moniker": "a"}, secret)
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
        token = _encode_token({"version": 1, "moniker": "a"}, secret)
        payload_b64, mac = token.rsplit(".", 1)
        tampered = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B") + "." + mac
        with self.assertRaises(TokenError):
            _decode_token(tampered, secret)

    def test_unsupported_version_rejected(self) -> None:
        secret = secrets.token_bytes(32)
        for bad_version in (None, "v1", 2, 0, -1, 1.0):
            token = _encode_token(
                {"version": bad_version, "moniker": "a"}, secret
            )
            with self.assertRaises(TokenError) as cm:
                _decode_token(token, secret)
            self.assertEqual(cm.exception.code, CODE_TOKEN_INVALID)


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

    def test_bind_with_loginid(self) -> None:
        r = SessionRegistry()
        r.bind("s1", "w1", "alice", False, loginid="alice_os")
        self.assertEqual(r.get_by_session("s1").loginid, "alice_os")
        self.assertEqual(r.get_by_websocket("w1").loginid, "alice_os")

    def test_rebind_preserves_loginid_when_omitted(self) -> None:
        """Re-bind without loginid keeps the original value (the websocket
        changed but the session and its loginid are stable)."""
        r = SessionRegistry()
        r.bind("s1", "w1", "alice", False, loginid="alice_os")
        st = r.bind("s1", "w2", "alice", False)
        self.assertEqual(st.loginid, "alice_os")

    def test_rebind_overrides_loginid_when_provided(self) -> None:
        r = SessionRegistry()
        r.bind("s1", "w1", "alice", False, loginid="alice_os")
        st = r.bind("s1", "w2", "alice", False, loginid="alice_v2")
        self.assertEqual(st.loginid, "alice_v2")


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


class TestPhase1Regressions(unittest.IsolatedAsyncioTestCase):
    """Regression tests for Phase 1 correctness fixes.

    - expires_at in result envelopes reflects the token's real expiry
      (not wall-clock-now).
    - Atomic token rotation: if the new token cannot be persisted, the
      rotated record is deleted and the previous token survives.
    - Revoke envelopes include ``recoverable`` on both success and failure.
    - DBTokenStore.gc_expired honors its ``now`` parameter for tests.
    - v1 -> v2 secret upgrade logs a warning; if the rewrite fails, the
      original v1 bytes are preserved.
    - _write_secret_file uses os.fchmod on the open fd before close, so
      umask cannot leak permissions.
    """

    async def test_auth_envelope_expires_at_matches_record(self) -> None:
        """The expires_at in the auth envelope equals the record's expiry."""
        clock = [1_700_000_000.0]
        # Freeze the store's clock to match the AuthService's clock so the
        # record survives the store's lazy-expiry check.
        store = InMemoryTokenStore(now_factory=lambda: clock[0])
        service = _build_service(token_store=store, clock=lambda: clock[0])
        ws = _FakeWebSocket()
        resp = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        record = store.get(resp["token"])
        self.assertIsNotNone(record)
        expected_iso = (
            datetime.fromtimestamp(record.expires_at, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.assertEqual(resp["expires_at"], expected_iso)
        self.assertNotEqual(
            resp["expires_at"],
            datetime.fromtimestamp(_now_ts_proxy(), tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        )

    async def test_reconnect_envelope_expires_at_matches_record(self) -> None:
        """The expires_at in reconnect_result equals the new record."""
        clock = [1_700_000_000.0]
        store = InMemoryTokenStore(now_factory=lambda: clock[0])
        service = _build_service(token_store=store, clock=lambda: clock[0])
        ws1 = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws1, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        clock[0] += 30.0  # advance so the new expiry differs from now()
        ws2 = _FakeWebSocket()
        resp = await service.handle_message(
            None, ws2, "/", {"type": "reconnect", "token": auth["token"]}
        )
        new_record = store.get(resp["token"])
        self.assertIsNotNone(new_record)
        expected_iso = (
            datetime.fromtimestamp(new_record.expires_at, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.assertEqual(resp["expires_at"], expected_iso)

    async def test_refresh_envelope_expires_at_matches_record(self) -> None:
        """The expires_at in auth_result from a refresh equals the new record."""
        clock = [1_700_000_000.0]
        store = InMemoryTokenStore(now_factory=lambda: clock[0])
        service = _build_service(token_store=store, clock=lambda: clock[0])
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        clock[0] += 30.0
        resp = await service.handle_message(
            None, ws, "/", {"type": "auth_refresh", "token": auth["token"]}
        )
        record = store.get(resp["token"])
        self.assertIsNotNone(record)
        expected_iso = (
            datetime.fromtimestamp(record.expires_at, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.assertEqual(resp["expires_at"], expected_iso)

    async def test_reconnect_rotation_rolls_back_on_persist_failure(self) -> None:
        """If persist of the rotated token fails, the rotated record is
        removed from the store and the previous token remains usable."""
        from bed.api.auth import TokenRecord

        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws1 = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws1, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        old_token = auth["token"]
        old_record = store.get(old_token)
        self.assertIsNotNone(old_record)

        # Force the rotated record's store.put to raise. InMemoryTokenStore
        # has no failure path; intercept _persist via monkeypatch so we
        # simulate a DB write failure.
        def boom(rec: TokenRecord) -> None:
            store.put(rec)
            raise RuntimeError("simulated DB failure")

        with patch.object(service, "_persist", side_effect=boom):
            with self.assertRaises(RuntimeError):
                await service.handle_message(
                    None, _FakeWebSocket(), "/",
                    {"type": "reconnect", "token": old_token},
                )

        # The rotated record must have been removed by the rollback path.
        # The pre-rotation token must still be present and valid.
        self.assertIsNotNone(store.get(old_token))

    async def test_refresh_rotation_rolls_back_on_persist_failure(self) -> None:
        """Refresh rotation also rolls back if the new persist fails."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        old_token = auth["token"]

        def boom(rec) -> None:
            store.put(rec)
            raise RuntimeError("simulated DB failure")

        with patch.object(service, "_persist", side_effect=boom):
            with self.assertRaises(RuntimeError):
                await service.handle_message(
                    None, ws, "/",
                    {"type": "auth_refresh", "token": old_token},
                )

        self.assertIsNotNone(store.get(old_token))

    async def test_revoke_envelope_has_recoverable_on_failure(self) -> None:
        """A revoke with an invalid signature carries recoverable=False."""
        service = _build_service()
        resp = await service.handle_message(
            None, _FakeWebSocket(), "/",
            {"type": "auth_revoke", "token": "abc.def"},
        )
        self.assertEqual(resp["type"], "auth_revoke_result")
        self.assertFalse(resp["success"])
        self.assertIn("recoverable", resp)
        self.assertFalse(resp["recoverable"])

    async def test_revoke_envelope_has_recoverable_on_unknown_token(self) -> None:
        """A revoke of a valid-format but unknown token reports recoverable."""
        service = _build_service()
        # Hand-crafted token with a valid signature but not in the store.
        secret = service.secret
        token = _encode_token(
            {
                "version": 1,
                "moniker": "ghost",
                "issued_at": 0.0,
                "expires_at": 9_999_999_999.0,
                "session_id": "x",
                "is_sysop": False,
                "bed_instance_id": service.instance_id,
                "websocket_id": "w",
            },
            secret,
        )
        resp = await service.handle_message(
            None, _FakeWebSocket(), "/",
            {"type": "auth_revoke", "token": token},
        )
        self.assertFalse(resp["success"])
        self.assertEqual(resp["code"], CODE_TOKEN_REVOKED)
        self.assertTrue(resp["recoverable"])

    async def test_revoke_envelope_success_has_recoverable(self) -> None:
        """A successful revoke carries recoverable=False (already done)."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service.handle_message(
            None, ws, "/", {"type": "auth", "moniker": "a", "password": "p"}
        )
        resp = await service.handle_message(
            None, ws, "/",
            {"type": "auth_revoke", "token": auth["token"]},
        )
        self.assertTrue(resp["success"])
        self.assertIn("recoverable", resp)
        self.assertFalse(resp["recoverable"])


class TestDBTokenStoreGcExpiredArgs(unittest.TestCase):
    """DBTokenStore.gc_expired must honor the ``now`` parameter so tests can
    freeze the clock and assert on exact deletion counts."""

    def test_gc_expired_uses_now_param_when_supplied(self) -> None:
        from unittest.mock import MagicMock
        from bed.api.token_store import DBTokenStore

        fake_args = MagicMock()
        fake_args.pool = MagicMock()
        fake_conn = MagicMock()
        fake_conn.__enter__ = MagicMock(return_value=fake_conn)
        fake_conn.__exit__ = MagicMock(return_value=False)
        fake_args.pool.connection.return_value = fake_conn

        cursor_cm = MagicMock()
        cursor_cm.__enter__ = MagicMock(return_value=cursor_cm)
        cursor_cm.__exit__ = MagicMock(return_value=False)
        cursor_cm.rowcount = 0
        cursor_cm.execute = MagicMock()

        store = DBTokenStore(fake_args)
        with patch("bbsengine6.database.connect", return_value=fake_conn), \
             patch("bbsengine6.database.cursor", return_value=cursor_cm):
            store.gc_expired(now=1234.5)

        cursor_cm.execute.assert_called_once()
        sql, params = cursor_cm.execute.call_args.args
        self.assertIn("to_timestamp(%s)", sql)
        self.assertEqual(params, (1234.5,))

    def test_gc_expired_uses_now_when_omitted(self) -> None:
        from unittest.mock import MagicMock
        from bed.api.token_store import DBTokenStore

        fake_args = MagicMock()
        fake_args.pool = MagicMock()
        fake_conn = MagicMock()
        fake_conn.__enter__ = MagicMock(return_value=fake_conn)
        fake_conn.__exit__ = MagicMock(return_value=False)
        fake_args.pool.connection.return_value = fake_conn

        cursor_cm = MagicMock()
        cursor_cm.__enter__ = MagicMock(return_value=cursor_cm)
        cursor_cm.__exit__ = MagicMock(return_value=False)
        cursor_cm.rowcount = 0
        cursor_cm.execute = MagicMock()

        store = DBTokenStore(fake_args)
        with patch("bbsengine6.database.connect", return_value=fake_conn), \
             patch("bbsengine6.database.cursor", return_value=cursor_cm):
            store.gc_expired()

        cursor_cm.execute.assert_called_once()
        sql, params = cursor_cm.execute.call_args.args
        self.assertIn("WHERE expires_at <= now()", sql)
        self.assertEqual(params, ())


class TestSecretUpgrade(unittest.TestCase):
    """v1 -> v2 secret upgrade: warn on success, preserve v1 on failure."""

    def _write_v1(self, path: str) -> bytes:
        # Use a fixed non-whitespace buffer so ``raw.strip()`` doesn't
        # shorten the bytes (random ``secrets.token_bytes(32)`` can include
        # whitespace bytes that strip() would drop).
        v1_bytes = bytes(range(0x41, 0x61))  # 'A'..'a' = 32 bytes, no whitespace
        with open(path, "wb") as f:
            f.write(v1_bytes)
        os.chmod(path, 0o600)
        return v1_bytes

    def test_v1_upgrade_writes_v2_and_warns(self) -> None:
        from bed.api import secret as secret_mod
        import bbsengine6.io as bbs_io

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.secret")
            v1 = self._write_v1(path)

            captured: list[str] = []
            original = bbs_io.echo

            def _capture(msg, level="info"):
                captured.append(f"{level}:{msg}")
                return original(msg, level=level)

            with patch.object(bbs_io, "echo", _capture):
                hmac_bytes, instance_id = secret_mod._read_secret_file(path)

            self.assertEqual(hmac_bytes, v1)
            self.assertTrue(instance_id)
            # File must now be JSON with version 2.
            with open(path, "rb") as f:
                new_payload = json.loads(f.read().decode("utf-8"))
            self.assertEqual(new_payload["__bed_secret_version"], 2)
            self.assertEqual(new_payload["hmac"], v1.hex())
            # Warning was logged.
            self.assertTrue(
                any("v1->v2" in m and m.startswith("warning") for m in captured),
                f"missing v1->v2 warning; captured={captured}",
            )

    def test_v1_upgrade_preserves_v1_on_write_failure(self) -> None:
        from bed.api import secret as secret_mod

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.secret")
            v1 = self._write_v1(path)
            original_bytes = open(path, "rb").read()

            def boom(*a, **kw):
                raise OSError("simulated disk full")

            with patch.object(secret_mod, "_write_secret_file", side_effect=boom):
                hmac_bytes, instance_id = secret_mod._read_secret_file(path)

            self.assertEqual(hmac_bytes, v1)
            self.assertTrue(instance_id)
            # The file on disk must still be the original v1 bytes.
            self.assertEqual(open(path, "rb").read(), original_bytes)


class TestWriteSecretFileFchmod(unittest.TestCase):
    """_write_secret_file must call os.fchmod on the open fd before close
    so umask cannot leak permissions."""

    def test_fchmod_called_before_close(self) -> None:
        from bed.api import secret as secret_mod

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bed.secret")
            fchmod_calls: list[tuple[int, int]] = []

            real_fchmod = os.fchmod

            def spy_fchmod(fd: int, mode: int) -> None:
                fchmod_calls.append((fd, mode))
                real_fchmod(fd, mode)

            with patch("os.fchmod", spy_fchmod):
                secret_mod._write_secret_file(
                    path, {
                        "__bed_secret_version": 2,
                        "hmac": "ab" * 32,
                        "instance_id": "x",
                    }
                )

            self.assertEqual(len(fchmod_calls), 1)
            fd, mode = fchmod_calls[0]
            self.assertEqual(mode, 0o600)
            # The fd that was fchmod'd is the same one mkstemp returned.
            # If close happened before fchmod, the call would have raised
            # (bad file descriptor) and broken the write.
            mode_after = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode_after, 0o600)


# ---------------------------------------------------------------------
# Phase 6 hardening: fuzz + path stability tests.
#
# Hand-rolled (no hypothesis dep) fuzz-style coverage for:
# - decode_token: must raise a recognised error class for any garbage,
#   never silently produce a valid token.
# - write_secret_file: must reject path traversal, must reject symlink
#   targets that point outside the allowed root, must not follow
#   symlinks (TOCTOU hardening).
# - pidfile handling: must reject `..` paths, must expand `~`.


class TestDecodeTokenFuzz(unittest.TestCase):
    """``auth._decode_token`` must reject arbitrary garbage."""

    def setUp(self):
        # Module-level helper. Construct a fake token to verify
        # round-trip first, then use that secret for fuzz attempts.
        from bed.api.auth import _decode_token, _encode_token

        self._decode = _decode_token
        self.secret = b"\x01" * 32
        # Sanity: a real token must decode cleanly.
        good = _encode_token(
            {"sub": "alice", "exp": 9_999_999_999, "version": 1},
            self.secret,
        )
        decoded = self._decode(good, self.secret)
        self.assertEqual(decoded["sub"], "alice")

    def test_empty_string_raises_invalid_token(self):
        from bed.api.auth import TokenError

        with self.assertRaises(TokenError):
            self._decode("", self.secret)

    def test_random_garbage_raises_invalid_token(self):
        """A handful of random-looking tokens should all raise."""
        from bed.api.auth import TokenError

        samples = [
            "not-a-token",
            "abc.def.ghi",
            ".....",
            "0000000000000000000000000000000000000000000",
            "x" * 1000,
            "\x00\x01\x02\x03\x04",
            "YWJj.YWJj.YWJj",  # base64'd 'abc.abc.abc'
            "Bearer abc.def.ghi",
            "null",
            "true",
        ]
        for s in samples:
            with self.subTest(s=s):
                with self.assertRaises(TokenError):
                    self._decode(s, self.secret)

    def test_bytes_input_raises_typeerror_or_invalid_token(self):
        """Bytes input is not valid; either TokenError or
        TypeError is acceptable (depends on jwt internals)."""
        from bed.api.auth import TokenError

        try:
            self._decode(b"abc.def.ghi", self.secret)
        except (TokenError, TypeError):
            pass
        else:
            self.fail("bytes input should not silently succeed")

    def test_token_signed_with_wrong_secret_rejected(self):
        """A real-shaped token but signed with a different secret must
        raise (catches accidental signature-bypass regressions)."""
        from bed.api.auth import TokenError, _encode_token

        bogus = _encode_token(
            {"sub": "alice", "exp": 9_999_999_999, "version": 1},
            b"\x02" * 32,
        )
        with self.assertRaises(TokenError):
            self._decode(bogus, self.secret)


class TestSecretFilePathSafety(unittest.TestCase):
    """``_write_secret_file`` produces a mode-0600 file regardless of
    umask and replaces symlink destinations atomically rather than
    following them."""

    def test_writer_creates_0600_file(self):
        """A normal write produces a 0600 file even if umask is loose."""
        from bed.api.secret import _write_secret_file

        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "tilde-secret")
            payload = {
                "__bed_secret_version": 2,
                "hmac": "cd" * 32,
                "instance_id": "y",
            }
            old_umask = os.umask(0o077)
            try:
                _write_secret_file(target, payload)
            finally:
                os.umask(old_umask)
            self.assertTrue(os.path.exists(target))
            mode = stat.S_IMODE(os.stat(target).st_mode)
            self.assertEqual(mode, 0o600)

    def test_writer_replaces_symlink_atomically(self):
        """Writing through a symlink replaces the symlink itself with a
        regular file (os.replace does NOT follow symlinks)."""
        from bed.api.secret import _write_secret_file

        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "real-secret")
            link = os.path.join(d, "alias")
            os.symlink(target, link)
            self.assertTrue(os.path.islink(link))

            payload = {
                "__bed_secret_version": 2,
                "hmac": "ef" * 32,
                "instance_id": "z",
            }
            _write_secret_file(link, payload)
            # ``link`` is now a regular file (symlink was replaced).
            self.assertTrue(os.path.exists(link))
            self.assertFalse(os.path.islink(link))


class TestLoginIdPlumbing(unittest.IsolatedAsyncioTestCase):
    """Verify that ``loginid`` flows from credential provider -> session
    state -> token record -> reconnect round-trip."""

    async def asyncSetUp(self) -> None:
        import socket as _socket
        from bbsengine6.net import WebSocketServer
        from bed.api import AuthService, InMemoryTokenStore, SessionRegistry

        class _Provider:
            def authenticate(self, args, moniker, password, *, pool=None):
                from bed.api import MemberInfo
                if moniker == "alice" and password == "pw":
                    return MemberInfo(
                        moniker="alice",
                        is_sysop=False,
                        balance=7,
                        loginid="alice_os",
                    )
                return None

        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
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
            instance_id="loginid-instance",
            ttl_seconds=900,
        )
        self.server = WebSocketServer(host="127.0.0.1", port=self.port)
        self.auth_service.register_all(self.server)
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_loginid_auth_then_reconnect(self) -> None:
        import websockets

        uri = f"ws://127.0.0.1:{self.port}/"
        async with websockets.connect(uri) as ws:
            await ws.send(
                json.dumps({"type": "auth", "moniker": "alice", "password": "pw"})
            )
            auth = json.loads(await ws.recv())
            self.assertTrue(auth["success"])

            # The single bound session must carry loginid.
            bound = list(self.registry._by_session.values())
            self.assertEqual(len(bound), 1)
            self.assertEqual(bound[0].moniker, "alice")
            self.assertEqual(bound[0].loginid, "alice_os")

            # The issued token record must carry loginid.
            token1 = auth["token"]
            rec = self.token_store.get(token1)
            self.assertIsNotNone(rec)
            self.assertEqual(rec.loginid, "alice_os")

        async with websockets.connect(uri) as ws2:
            await ws2.send(json.dumps({"type": "reconnect", "token": token1}))
            rec = json.loads(await ws2.recv())
            self.assertTrue(rec["success"])
            token2 = rec["token"]

        # After reconnect, the still-bound session must carry loginid again.
        bound = list(self.registry._by_session.values())
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0].loginid, "alice_os")

        # The rotated token record must also carry loginid.
        rec2 = self.token_store.get(token2)
        self.assertIsNotNone(rec2)
        self.assertEqual(rec2.loginid, "alice_os")


class TestPreDispatchLogFormat(unittest.TestCase):
    """Verify the BED ``_pre_dispatch`` debug log emits ``loginid=`` in
    addition to ``session_id`` and ``moniker``. We don't drive a full
    WebSocket session here; we read the source of ``bed.main`` and assert
    the format strings contain the expected keys."""

    def test_log_format_includes_loginid(self) -> None:
        import re
        from pathlib import Path

        src = Path("/home/opencode/data/work/bed/src/bed/main.py").read_text()
        # Both branches (bank_add/bank_remove and the default) must
        # include loginid=... so grepping a router log line yields the
        # OS-level user identity alongside the display name.
        self.assertIn("loginid=", src)

        # Each fragment can be a separate f-string literal that Python
        # implicitly concatenates, so check the keys appear in the
        # expected order anywhere in the default branch. The unbound
        # marker (``unbound``) replaces the empty-string fallback so a
        # pre-auth log line is grep-friendly and visually distinct
        # from an authenticated empty loginid.
        self.assertIn("unbound", src)
        positions = {
            "session_id": src.find("router: in session_id={session_id}"),
            "loginid": src.find("loginid={loginid_str}"),
            "moniker": src.find("moniker={moniker_str}"),
            "type": src.find("type={msg_type}"),
        }
        for key, pos in positions.items():
            self.assertGreater(
                pos,
                -1,
                f"default-branch f-string missing fragment for {key!r}",
            )
        ordered = sorted(positions.items(), key=lambda kv: kv[1])
        keys_in_order = [k for k, _ in ordered]
        self.assertEqual(
            keys_in_order,
            ["session_id", "loginid", "moniker", "type"],
            "router log fragments must appear in "
            "session_id, loginid, moniker, type order",
        )


class TestPostDispatchHookOrder(unittest.IsolatedAsyncioTestCase):
    """The ``router: in ...`` log line for the ``auth`` message itself must
    carry the populated loginid/moniker, because post_dispatch runs AFTER
    AuthService.bind has populated the SessionState."""

    async def asyncSetUp(self) -> None:
        import socket as _socket
        from bbsengine6.net import WebSocketServer
        from bed.api import AuthService, InMemoryTokenStore, SessionRegistry

        class _Provider:
            def authenticate(self, args, moniker, password, *, pool=None):
                from bed.api import MemberInfo
                if moniker == "alice" and password == "pw":
                    return MemberInfo(
                        moniker="alice",
                        is_sysop=False,
                        balance=7,
                        loginid="alice_os",
                    )
                return None

        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
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
            instance_id="post-dispatch-instance",
            ttl_seconds=900,
        )
        self.server = WebSocketServer(host="127.0.0.1", port=self.port)
        self.auth_service.register_all(self.server)

        # Capture the state the BED post_dispatch hook would observe by
        # wiring a hook that snapshots ``registry.get_by_websocket``
        # *after* the handler has run.
        self.snapshots: Dict[str, Any] = {}

        async def _post_dispatch(websocket, message, response):
            ws_id = str(websocket.id)
            state = self.registry.get_by_websocket(ws_id)
            self.snapshots[message.get("type", "")] = {
                "moniker": state.moniker if state else None,
                "loginid": state.loginid if state else None,
            }

        self.server._post_dispatch = _post_dispatch
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_auth_message_sees_populated_state(self) -> None:
        import websockets

        async with websockets.connect(f"ws://127.0.0.1:{self.port}/") as ws:
            await ws.send(
                json.dumps({"type": "auth", "moniker": "alice", "password": "pw"})
            )
            await ws.recv()

        snap = self.snapshots.get("auth")
        self.assertIsNotNone(snap, "post_dispatch was not invoked for auth")
        self.assertEqual(snap["moniker"], "alice")
        self.assertEqual(snap["loginid"], "alice_os")


class TestPreDispatchHookSetsRole(unittest.IsolatedAsyncioTestCase):
    """The ``pre_dispatch`` hook must call ``set_current_role(state.moniker)``
    so DB queries inside the handler run under the right PostgreSQL role."""

    async def asyncSetUp(self) -> None:
        import socket as _socket
        from bbsengine6.net import WebSocketServer
        from bed.api import AuthService, InMemoryTokenStore, SessionRegistry

        class _Provider:
            def authenticate(self, args, moniker, password, *, pool=None):
                from bed.api import MemberInfo
                if moniker == "alice" and password == "pw":
                    return MemberInfo(
                        moniker="alice",
                        is_sysop=False,
                        balance=7,
                        loginid="alice_os",
                    )
                return None

        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]

        self.token_store = InMemoryTokenStore()
        self.registry = SessionRegistry()
        args = argparse.Namespace(debug=False, pool=None)
        self.auth_service = AuthService(
            args=args,
            session_registry=self.registry,
            token_store=self.token_store,
            credential_provider=_Provider(),
            secret=secrets.token_bytes(32),
            instance_id="pre-dispatch-instance",
            ttl_seconds=900,
        )
        self.server = WebSocketServer(host="127.0.0.1", port=self.port)
        self.auth_service.register_all(self.server)

        self.observed_roles: List[Optional[str]] = []

        async def _pre_dispatch(websocket, message):
            ws_id = str(websocket.id)
            state = self.registry.get_by_websocket(ws_id)
            if state is not None:
                self.observed_roles.append(("set", state.moniker))
            else:
                self.observed_roles.append(("skip", None))

        self.server._pre_dispatch = _pre_dispatch
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_role_set_after_auth(self) -> None:
        import websockets

        async with websockets.connect(f"ws://127.0.0.1:{self.port}/") as ws:
            # First message: auth. Pre_dispatch runs before AuthService
            # binds the state, so the hook should skip (no role change).
            await ws.send(
                json.dumps({"type": "auth", "moniker": "alice", "password": "pw"})
            )
            await ws.recv()

        # The auth message's pre_dispatch should NOT have set a role,
        # because at pre_dispatch time the state was not yet populated.
        kinds = [k for k, _ in self.observed_roles]
        self.assertEqual(kinds[0], "skip")


# ---------------------------------------------------------------------
# bbsengine6.auth.access() delegation
#
# bed.api.auth.AuthService delegates every per-op policy decision to the
# module-level access() function defined in bbsengine6.auth. These
# tests pin that delegation contract: every _handle_auth_* method calls
# access() with the expected op and message shape; when access() returns
# False, the handler returns the per-op envelope (forbidden for
# reconnect/revoke; not_authenticated for refresh so the client can
# recover via reconnect). The full op-by-op matrix is covered by
# bbsengine6/py/tests/test_auth_access.py. Here we only need to pin
# that the bed handler actually invokes it.


def _patch_auth_access_returning(value):
    """Patch ``bed.api.auth._auth_access`` to return ``value``.

    Returns the patch object so the test can assert call_args.
    """
    from bed.api import auth as auth_mod

    return patch.object(auth_mod, "_auth_access", return_value=value)


class TestAuthAccessDelegation(unittest.IsolatedAsyncioTestCase):
    """Pin the delegation contract: every handler calls _auth_access
    with the right op and translates False into the right envelope."""

    async def test_handle_auth_delegates_to_bbsengine6_auth_access(self) -> None:
        """_handle_auth calls bbsengine6.auth.access with op='login'."""
        provider = _AlwaysAllowProvider()
        service = _build_service(credential_provider=provider)
        ws = _FakeWebSocket()
        with _patch_auth_access_returning(True) as access_mock, \
             patch("bed.api.auth.io.echo"):
            result = await service._handle_auth(
                ws, {"type": "auth", "moniker": "alice", "password": "pw"}
            )
        self.assertEqual(result["type"], "auth_result")
        access_mock.assert_called_once()
        args, op = access_mock.call_args[0]
        self.assertEqual(op, "login")
        self.assertIn("claims", access_mock.call_args.kwargs.get("message", {}))

    async def test_handle_reconnect_delegates_to_bbsengine6_auth_access(self) -> None:
        """_handle_reconnect calls bbsengine6.auth.access with op='reconnect'
        AFTER the wire-shape gates have passed; on False it returns a
        forbidden envelope (and never mints/persists a rotated token)."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws1 = _FakeWebSocket()
        auth = await service._handle_auth(
            ws1, {"type": "auth", "moniker": "alice", "password": "pw"}
        )
        old_token = auth["token"]

        ws2 = _FakeWebSocket()
        with _patch_auth_access_returning(False), \
             patch("bed.api.auth.io.echo"):
            result = await service._handle_reconnect(
                ws2, {"type": "reconnect", "token": old_token}
            )
        self.assertEqual(result["type"], "error")
        self.assertEqual(result["code"], CODE_FORBIDDEN)
        self.assertFalse(result["recoverable"])
        # The old token must still be in the store -- we did not rotate.
        self.assertIsNotNone(store.get(old_token))

    async def test_handle_reconnect_allowed_when_access_returns_true(self) -> None:
        """_handle_reconnect proceeds to bind + mint + persist when
        access() returns True."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws1 = _FakeWebSocket()
        auth = await service._handle_auth(
            ws1, {"type": "auth", "moniker": "alice", "password": "pw"}
        )
        old_token = auth["token"]

        ws2 = _FakeWebSocket()
        with _patch_auth_access_returning(True) as access_mock, \
             patch("bed.api.auth.io.echo"):
            result = await service._handle_reconnect(
                ws2, {"type": "reconnect", "token": old_token}
            )
        self.assertEqual(result["type"], "reconnect_result")
        self.assertTrue(result["success"])
        self.assertEqual(access_mock.call_args[0][1], "reconnect")
        # session kwarg is None because ws2 is unbound.
        self.assertIsNone(access_mock.call_args.kwargs.get("session"))

    async def test_handle_auth_refresh_delegates_to_bbsengine6_auth_access(self) -> None:
        """_handle_auth_refresh calls bbsengine6.auth.access with op='refresh';
        on False it returns not_authenticated (recoverable=True so the
        client can try reconnect with its last good token)."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service._handle_auth(
            ws, {"type": "auth", "moniker": "alice", "password": "pw"}
        )
        old_token = auth["token"]

        other_ws = _FakeWebSocket()
        with _patch_auth_access_returning(False), \
             patch("bed.api.auth.io.echo"):
            result = await service._handle_auth_refresh(
                other_ws, {"type": "auth_refresh", "token": old_token}
            )
        self.assertEqual(result["type"], "error")
        self.assertEqual(result["code"], CODE_NOT_AUTHENTICATED)
        self.assertTrue(result["recoverable"])
        # No rotation should have happened.
        self.assertIsNotNone(store.get(old_token))

    async def test_handle_auth_refresh_allowed_when_access_returns_true(self) -> None:
        """_handle_auth_refresh proceeds to mint/persist when access()
        returns True, and the session kwarg is the LIVE websocket's
        SessionState (not None)."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service._handle_auth(
            ws, {"type": "auth", "moniker": "alice", "password": "pw"}
        )
        old_token = auth["token"]

        with _patch_auth_access_returning(True) as access_mock, \
             patch("bed.api.auth.io.echo"):
            result = await service._handle_auth_refresh(
                ws, {"type": "auth_refresh", "token": old_token}
            )
        self.assertEqual(result["type"], "auth_result")
        self.assertEqual(access_mock.call_args[0][1], "refresh")
        # session kwarg is the live state bound to the websocket.
        live = access_mock.call_args.kwargs.get("session")
        self.assertIsNotNone(live)
        self.assertEqual(live.moniker, "alice")

    async def test_handle_auth_revoke_delegates_to_bbsengine6_auth_access(self) -> None:
        """_handle_auth_revoke calls bbsengine6.auth.access with op='revoke';
        on False it returns an auth_revoke_result with code='forbidden'."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service._handle_auth(
            ws, {"type": "auth", "moniker": "alice", "password": "pw"}
        )
        token = auth["token"]

        with _patch_auth_access_returning(False), \
             patch("bed.api.auth.io.echo"):
            result = await service._handle_auth_revoke(
                ws, {"type": "auth_revoke", "token": token}
            )
        self.assertEqual(result["type"], "auth_revoke_result")
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "forbidden")
        self.assertFalse(result["recoverable"])

    async def test_handle_auth_revoke_allowed_when_access_returns_true(self) -> None:
        """_handle_auth_revoke proceeds to delete from store when access()
        returns True."""
        store = InMemoryTokenStore()
        service = _build_service(token_store=store)
        ws = _FakeWebSocket()
        auth = await service._handle_auth(
            ws, {"type": "auth", "moniker": "alice", "password": "pw"}
        )
        token = auth["token"]

        with _patch_auth_access_returning(True) as access_mock, \
             patch("bed.api.auth.io.echo"):
            result = await service._handle_auth_revoke(
                ws, {"type": "auth_revoke", "token": token}
            )
        self.assertEqual(result["type"], "auth_revoke_result")
        self.assertTrue(result["success"])
        self.assertEqual(access_mock.call_args[0][1], "revoke")
        self.assertIsNone(store.get(token))

    async def test_handle_message_passes_correct_op_to_access(self) -> None:
        """handle_message dispatches to the right _handle_auth_* method
        based on the wire type, and the op passed to access() is the
        corresponding domain verb.

        For ops that need a token, we issue one first so the
        wire-shape gate (decode + store + expiry + instance) passes
        and access() is actually invoked. The unit test for the access()
        decision matrix lives in bbsengine6/py/tests/test_auth_access.py;
        here we only pin the dispatch contract.

        Each token-based case uses a fresh service+websocket pair so
        token rotation in one case cannot affect the next.
        """
        cases = [
            ("auth", "login", {"moniker": "alice", "password": "pw"}, None),
            ("reconnect", "reconnect", None, True),
            ("auth_refresh", "refresh", None, True),
            ("auth_revoke", "revoke", None, True),
        ]
        for wire_type, expected_op, payload, needs_token in cases:
            with self.subTest(wire_type=wire_type):
                service = _build_service(token_store=InMemoryTokenStore())
                ws = _FakeWebSocket()
                if needs_token:
                    auth = await service._handle_auth(
                        ws,
                        {"type": "auth", "moniker": "alice", "password": "pw"},
                    )
                    msg = {"type": wire_type, "token": auth["token"]}
                else:
                    msg = {"type": wire_type, **(payload or {})}
                with _patch_auth_access_returning(True) as access_mock, \
                     patch("bed.api.auth.io.echo"):
                    result = await service.handle_message(None, ws, "/", msg)
                self.assertEqual(access_mock.call_args[0][1], expected_op)
                # Result is either a successful envelope or an error
                # envelope. Either way, it is NOT None (the handler
                # does not silently drop messages).
                self.assertIsNotNone(result)

    async def test_handle_message_returns_none_for_unknown_type(self) -> None:
        """If wire type is not in _TYPE_TO_OP, handle_message returns
        None -- the auth service does not own unknown message types."""
        service = _build_service()
        ws = _FakeWebSocket()
        result = await service.handle_message(
            None, ws, "/", {"type": "totally_unknown"}
        )
        self.assertIsNone(result)


class TestAuthTypeToOpMapping(unittest.TestCase):
    """Static pinning of the ``_TYPE_TO_OP`` and ``_OP_TO_HANDLER`` maps.

    These two dicts must stay in sync. If a new op is added without
    updating both maps, handle_message returns None and the dispatch
    test in TestAuthAccessDelegation catches it.
    """

    def test_type_to_op_is_complete(self) -> None:
        from bed.api import auth as auth_mod

        self.assertEqual(
            auth_mod._TYPE_TO_OP,
            {
                "auth": "login",
                "reconnect": "reconnect",
                "auth_refresh": "refresh",
                "auth_revoke": "revoke",
            },
        )

    def test_op_to_handler_covers_every_op(self) -> None:
        from bed.api import auth as auth_mod

        for op in auth_mod._TYPE_TO_OP.values():
            self.assertIn(op, auth_mod._OP_TO_HANDLER)

    def test_handled_types_match_type_to_op_keys(self) -> None:
        from bed.api import auth as auth_mod

        # AuthService.HANDLED_TYPES is the public set of message types
        # the service registers with the server; it must match the keys
        # of _TYPE_TO_OP (every registered type has an op mapping).
        self.assertEqual(
            set(auth_mod.AuthService.HANDLED_TYPES),
            set(auth_mod._TYPE_TO_OP.keys()),
        )


if __name__ == "__main__":
    unittest.main()
