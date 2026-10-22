"""BedAuthServiceClient.login passes bed's last_login through to the caller."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from bed.client.authservice import BedAuthServiceClient


def _client(reply):
    conn = MagicMock()
    conn.send = AsyncMock(return_value=reply)
    return BedAuthServiceClient(conn)


def test_login_result_carries_last_login_when_bed_sends_it():
    reply = {"type": "auth_result", "success": True, "moniker": "jam", "token": "t",
             "session_id": "s", "expires_at": "x", "balance": 5, "last_login": "2026-10-22T14:52:26+00:00"}
    r = asyncio.run(_client(reply).login("jam", "pw"))
    assert r["ok"] is True
    assert r["last_login"] == "2026-10-22T14:52:26+00:00"


def test_login_result_has_last_login_none_for_a_first_login():
    reply = {"type": "auth_result", "success": True, "moniker": "bob", "token": "t",
             "session_id": "s", "expires_at": "x", "balance": 5, "last_login": None}
    r = asyncio.run(_client(reply).login("bob", "pw"))
    assert r["last_login"] is None
