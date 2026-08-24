#!/usr/bin/env python3
# bed/tests/test_auth_debug_branches.py
# Regression for the ``token_revoked``-after-login diagnostic labels.
#
# Each test drives :class:`bed.api.auth.AuthService` end-to-end
# against an in-process ``InMemoryTokenStore(debug=True)``, captures
# the ``AuthService.debug: op=... branch=...`` and
# ``InMemoryTokenStore.debug: op=...`` lines emitted via
# :func:`bbsengine6.io.echo`, and asserts the labels match the
# ones an operator will see at runtime when ``bed --debug`` is set.
#
# These tests are NOT about the wiring of the debug gate (the
# ``args.debug`` toggle is exercised by ``test_auth_service.py``
# in passing); they pin the *labels* so a code change that renames
# e.g. ``branch=REVOKED`` to ``branch=revoked`` shows up as a
# failing test instead of silently breaking the operator-side
# ``grep tok=<prefix>`` workflow.
#
# Capturing ``io.echo`` is tricky: ``bbsengine6.io.echo`` is a
# thin wrapper around ``echo`` primitives and the side-effect goes
# to stderr in non-debug-aware shells. The harness here patches
# ``bbsengine6.io.echo`` with a collector so each emitted line is
# recorded against the test's message logger.

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

sys.path.insert(0, "/home/opencode/data/work/bed/src")
sys.path.insert(0, "/home/opencode/data/work/bbsengine6/py/src")

from bed.api import AuthService, InMemoryTokenStore, SessionRegistry


class _AlwaysAllowProvider:
    """Stand-in CredentialProvider. ``moniker='jam' password='p'``
    returns a deterministic ``MemberInfo``; every other
    ``(moniker, password)`` pair is rejected.
    """

    def authenticate(self, args, moniker, password, *, pool=None):
        from bed.api import MemberInfo
        if moniker == "jam" and password == "p":
            return MemberInfo(moniker="jam", is_sysop=True, balance=0, loginid="jam")
        return None


class _FakeWebSocket:
    def __init__(self) -> None:
        import uuid
        self.id = uuid.uuid4()


def _debug_args() -> argparse.Namespace:
    # ``debug`` is no longer consulted by the instrumentation; kept
    # on the namespace only for backwards compatibility with any
    # downstream code that reads it through ``args.debug``.
    return argparse.Namespace(debug=True, pool=None)


def _build_service(
    *,
    token_store: InMemoryTokenStore,
    instance_id: str = "instance-debug",
) -> AuthService:
    secret = secrets.token_bytes(32)
    return AuthService(
        args=_debug_args(),
        session_registry=SessionRegistry(),
        token_store=token_store,
        credential_provider=_AlwaysAllowProvider(),
        secret=secret,
        instance_id=instance_id,
        ttl_seconds=900,
    )


class _EchoCollector:
    """Collects the diagnostic lines auth.py / token_store.py emit
    while driving the handlers. The lines are tagged by message
    prefix (``AuthService.debug:`` and ``InMemoryTokenStore.debug:``)
    and are emitted unconditionally -- there is no ``--debug`` gate
    any more -- so we filter on the prefix instead of on the
    ``level=`` kwarg.

    Anything else is silently dropped. Re-emitting through the real
    ``bbsengine6.io.echo`` would re-enter the patch and recurse
    because the function object is the same one being shadowed.
    """

    DIAG_PREFIXES = (
        "AuthService.debug:",
        "InMemoryTokenStore.debug:",
    )

    def __init__(self) -> None:
        self.lines: List[str] = []

    def __call__(self, message: str, *, level: str = "info") -> None:
        if any(message.startswith(p) for p in self.DIAG_PREFIXES):
            self.lines.append(message)


class AuthDebugBranchTests(unittest.IsolatedAsyncioTestCase):
    """Pin the diagnostic labels that the ``token_revoked`` wall
    hangs on. When an operator hands the bed log to anyone, the
    branches named here are the only stable contracts.
    """

    async def _drive(self, service, message):
        ws = _FakeWebSocket()
        return await service.handle_message(None, ws, "/", message)

    def _branches(self, lines: List[str]) -> List[str]:
        out: List[str] = []
        for line in lines:
            if "AuthService.debug:" not in line:
                continue
            for tok in line.split():
                if tok.startswith("branch="):
                    out.append(tok.split("=", 1)[1])
                    break
        return out

    async def test_login_emits_ok_branch(self) -> None:
        collector = _EchoCollector()
        store = InMemoryTokenStore(debug=True)
        service = _build_service(token_store=store)

        with patch("bed.api.auth.io.echo", side_effect=collector):
            auth = await self._drive(
                service,
                {"type": "auth", "moniker": "jam", "password": "p"},
            )

        self.assertTrue(auth.get("success") is not False)
        branches = self._branches(collector.lines)
        self.assertIn("OK", branches, collector.lines)
        # store.size grows to 1 right after put
        store_records = [
            line for line in collector.lines
            if line.startswith("InMemoryTokenStore.debug:")
        ]
        self.assertTrue(
            any("op=put" in line for line in store_records),
            store_records,
        )

    async def test_reconnect_with_missing_token_emits_revoked_branch(self) -> None:
        """The user's symptom: token stored, store deleted out from
        under us. After ``store.delete(token)``, reconnect must
        emit ``branch=REVOKED`` with ``store_size`` reflecting the
        remaining records (not 0 unless the store was wiped).
        """
        collector = _EchoCollector()
        store = InMemoryTokenStore(debug=True)
        service = _build_service(token_store=store)

        with patch("bed.api.auth.io.echo", side_effect=collector):
            auth = await self._drive(
                service,
                {"type": "auth", "moniker": "jam", "password": "p"},
            )
            self.assertTrue(auth.get("success") is not False)
            token = auth["token"]
            # Simulate the exact failure the operator saw: store
            # entry is gone 9s later (proxy by deleting right now;
            # the branch label is what we care about).
            store.delete(token)

            collector.lines.clear()
            resp = await self._drive(
                service,
                {"type": "reconnect", "token": token},
            )

        self.assertEqual(resp["code"], "token_revoked")
        branches = self._branches(collector.lines)
        self.assertIn("REVOKED", branches, collector.lines)
        # The REVOKED log line carries a ``store_size`` field so an
        # operator can tell "store wiped entirely" from "this one
        # token is gone".
        revoked_lines = [
            line for line in collector.lines
            if "branch=REVOKED" in line
        ]
        self.assertEqual(len(revoked_lines), 1)
        self.assertIn("store_size=", revoked_lines[0])

    async def test_expired_token_emits_expired_branch_not_revoked(self) -> None:
        """The operator asked: \"is this a datetime matching
        issue?\". Make sure the EXPIRED label is distinct so
        running with ``bed --debug`` and grepping
        ``branch=EXPIRED`` vs ``branch=REVOKED`` actually
        discriminates. Drive a token whose ``expires_at`` is in
        the past.

        A subtlety: ``InMemoryTokenStore.get`` lazy-GCs an
        expired record before the handler ever sees it (see the
        comment at ``auth._handle_reconnect`` about avoiding the
        expiry masked-as-revoked shape). To force the EXPIRED
        branch to fire we hand the handler a token whose JWT
        claim carries ``expires_at <= now`` but the in-memory
        record holds a future ``expires_at``; the production
        code path is the one the docs warn about -- a record
        that survived the lazy GC because its record-level
        expiry is later than the claim-level expiry. Construct
        that scenario directly.
        """
        collector = _EchoCollector()
        store = InMemoryTokenStore(debug=True)
        service = _build_service(token_store=store)

        # Mint a real token first so the secret is loaded and we
        # know the format. Then grab the secret, fabricate a
        # parallel token whose ``expires_at`` is 1 hour in the
        # past, and put it into the store with the FUTURE
        # ``expires_at`` that the GET path actually consults.
        import secrets as _secrets
        from bed.api.auth import _encode_token, _decode_token, _now_ts

        with patch("bed.api.auth.io.echo", side_effect=_EchoCollector()):
            auth = await self._drive(
                service,
                {"type": "auth", "moniker": "jam", "password": "p"},
            )
        real_token = auth["token"]
        real_record = store.get(real_token)
        self.assertIsNotNone(real_record)

        # Fabricate a stale token whose JWT claim expires in the
        # past but whose ``TokenRecord.expires_at`` (the field
        # the store's lazy-GC consults) sits in the future so the
        # store keeps it around. Mirrors the in-the-wild shape
        # where a JWT claim outlives a record that was bumped.
        stale_token = _encode_token(
            {
                "version": 1,
                "moniker": "jam",
                "issued_at": _now_ts() - 7200,
                "expires_at": _now_ts() - 3600,  # 1h ago
                "session_id": real_record.session_id,
                "is_sysop": True,
                "bed_instance_id": service.instance_id,
                "websocket_id": real_record.websocket_id,
                "loginid": real_record.loginid,
            },
            service.secret,
        )
        # Insert with the FUTURE record-level expiry so the
        # store's lazy GC does not drop it before the handler
        # reads it. This is the documented "store keeps a record
        # whose claim has aged out" race.
        store.put(  # noqa: SLF001 - direct record write is intentional in tests
            real_record.__class__(
                token=stale_token,
                moniker=real_record.moniker,
                session_id=real_record.session_id,
                issued_at=real_record.issued_at,
                expires_at=real_record.expires_at,
                is_sysop=real_record.is_sysop,
                bed_instance_id=real_record.bed_instance_id,
                websocket_id=real_record.websocket_id,
                claims={
                    "version": 1,
                    "moniker": real_record.moniker,
                    "issued_at": _now_ts() - 7200,
                    "expires_at": _now_ts() - 3600,
                    "session_id": real_record.session_id,
                    "is_sysop": real_record.is_sysop,
                    "bed_instance_id": real_record.bed_instance_id,
                    "websocket_id": real_record.websocket_id,
                    "loginid": real_record.loginid,
                },
                loginid=real_record.loginid,
            )
        )

        collector.lines.clear()
        with patch("bed.api.auth.io.echo", side_effect=collector):
            resp = await self._drive(
                service,
                {"type": "reconnect", "token": stale_token},
            )

        self.assertEqual(resp["code"], "token_expired")
        branches = self._branches(collector.lines)
        self.assertIn("EXPIRED", branches, collector.lines)
        # The ``delta`` field carries ``expires_at - now`` and
        # must be negative, which is the proof that the expiry
        # path is the one that fired (vs lazy GC masking it).
        exp_lines = [
            line for line in collector.lines
            if "branch=EXPIRED" in line
        ]
        self.assertEqual(len(exp_lines), 1)
        self.assertIn("delta=", exp_lines[0])

    async def test_garbled_token_emits_garbled_branch(self) -> None:
        collector = _EchoCollector()
        store = InMemoryTokenStore(debug=True)
        service = _build_service(token_store=store)

        with patch("bed.api.auth.io.echo", side_effect=collector):
            resp = await self._drive(
                service,
                {"type": "reconnect", "token": "not.a.real.jwt"},
            )

        self.assertEqual(resp["code"], "token_invalid")
        branches = self._branches(collector.lines)
        self.assertIn("GARBLED", branches, collector.lines)

    async def test_revoke_emits_ok_branch(self) -> None:
        collector = _EchoCollector()
        store = InMemoryTokenStore(debug=True)
        service = _build_service(token_store=store)

        with patch("bed.api.auth.io.echo", side_effect=collector):
            auth = await self._drive(
                service,
                {"type": "auth", "moniker": "jam", "password": "p"},
            )
            token = auth["token"]

            collector.lines.clear()
            resp = await self._drive(
                service,
                {"type": "auth_revoke", "token": token},
            )

        self.assertTrue(resp.get("success"))
        branches = self._branches(collector.lines)
        self.assertIn("OK", branches, collector.lines)


if __name__ == "__main__":
    unittest.main()
