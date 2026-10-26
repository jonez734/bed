"""Wire-level tests for bed's casino_* WebSocket messages, against an in-process server.

These run a real WebSocketServer with AuthService and CasinoService registered, on an ephemeral
port. No daemon, credentials, or database are needed: the casino service's database hook (_conn)
is replaced with a fake that records the call and returns a fixed result. So these tests check
the wire contract: authentication is required, every casino_* type gets a reply in the shape the
client reads, and a refused request never reaches the database.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

sys.path.insert(0, "/home/opencode/data/work/bed/src")

import websockets  # noqa: E402

from bed.api import casino as casino_mod  # noqa: E402
from bed.tests._auth_helpers import (  # noqa: E402
    StubCredentialProvider,
    _send_and_recv,
    _start_bed_with_auth,
)


class _FakeCursor:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.store.append((sql, params))
        self.last = params

    def fetchone(self):
        return (self.store.result,)


class _FakeConn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self.store)


class _Store(list):
    """Records every database call and returns a fixed jsonb result."""

    result = {"success": True, "tables": []}


def _login_payload():
    return {"type": "auth", "moniker": "alice", "password": "pw"}


async def _connect_authed(port):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}/")
    reply = await _send_and_recv(ws, _login_payload())
    token = reply.get("token")
    return ws, token


@asynccontextmanager
async def _casino_server(result=None):
    """A bed server with auth and the casino service; the database is a recording fake."""
    store = _Store()
    if result is not None:
        store.result = result
    server, port, registry, auth_service = await _start_bed_with_auth()
    from bed.api.casino import CasinoService
    svc = CasinoService(
        type("A", (), {"_casino_config": {"clock": {"max_turn_seconds": 120}}})(),
        registry,
        secret=auth_service.secret,
        token_store=auth_service.token_store,
        instance_id=auth_service.instance_id,
    )
    svc._conn = lambda: _FakeConn(store)
    svc.register_all(server)
    try:
        yield port, store
    finally:
        await server.stop()


class CasinoWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_casino_message_is_refused_before_the_database(self):
        async with _casino_server() as (port, store):
            ws = await websockets.connect(f"ws://127.0.0.1:{port}/")
            try:
                reply = await _send_and_recv(ws, {"type": "casino_list_tables"})
            finally:
                await ws.close()
        self.assertEqual(reply.get("type"), "error", reply)
        self.assertEqual(store, [], "a refused request must not reach the database")

    async def test_authenticated_list_gets_a_table_list_reply(self):
        async with _casino_server({"success": True, "tables": [{"moniker": "t1"}]}) as (port, store):
            ws, token = await _connect_authed(port)
            try:
                reply = await _send_and_recv(ws, {"type": "casino_list_tables", "token": token})
            finally:
                await ws.close()
        self.assertEqual(reply.get("type"), "table_list", reply)
        self.assertEqual(reply.get("tables"), [{"moniker": "t1"}])
        self.assertEqual(len(store), 1, "exactly one database call for one list")

    async def test_create_over_the_ceiling_is_refused_before_the_database(self):
        async with _casino_server() as (port, store):
            ws, token = await _connect_authed(port)
            try:
                reply = await _send_and_recv(ws, {
                    "type": "casino_create_table", "token": token, "game_type": "blackjack",
                    "moniker": "t9", "turn_seconds": 999,
                })
            finally:
                await ws.close()
        self.assertEqual(reply.get("code"), "turn_seconds_over_ceiling", reply)
        self.assertEqual(store, [], "an over-ceiling create must not reach the database")

    async def test_every_casino_type_gets_a_reply(self):
        types = [
            ("casino_list_tables", {}),
            ("casino_get_table", {"moniker": "t1"}),
            ("casino_create_table", {"game_type": "blackjack", "moniker": "t1"}),
            ("casino_update_table", {"moniker": "t1", "status": "closed"}),
            ("casino_join_table", {"moniker": "t1"}),
            ("casino_leave_table", {"moniker": "t1"}),
        ]
        async with _casino_server({"success": True, "table": None, "tables": [], "message": "ok"}) as (port, _store):
            ws, token = await _connect_authed(port)
            try:
                for msg_type, fields in types:
                    reply = await _send_and_recv(ws, {"type": msg_type, "token": token, **fields})
                    self.assertIn("type", reply, f"{msg_type} got no typed reply: {reply}")
                    self.assertNotEqual(reply.get("type"), None, msg_type)
            finally:
                await ws.close()
