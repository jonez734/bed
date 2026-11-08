"""A server refusal raises BedUnavailable with the server's code; a transport failure has none.

Regression: an expired token came back as ``token_revoked`` in the message text only, so callers
(and the shell's balance reason) could not tell a refused token from a dead daemon.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bed.client.bank import BedBankClient
from bed.client.connection import BedConnection
from bed.client.exceptions import BedUnavailable


def _conn(reply=None, exc=None):
    # A real BedConnection, so the client uses it instead of dialing a daemon; only send is faked.
    conn = BedConnection.__new__(BedConnection)
    conn.send = AsyncMock(side_effect=exc) if exc is not None else AsyncMock(return_value=reply)
    return conn


def test_server_refusal_carries_the_error_code():
    conn = _conn({"type": "error", "code": "token_revoked", "message": "Token is no longer valid"})
    with pytest.raises(BedUnavailable) as info:
        asyncio.run(BedBankClient(conn).get_balance_result("jam", token="t"))
    assert info.value.code == "token_revoked"
    assert "token_revoked" in str(info.value)


def test_transport_failure_has_no_code():
    conn = _conn(exc=BedUnavailable("connection refused"))
    with pytest.raises(BedUnavailable) as info:
        asyncio.run(BedBankClient(conn).get_balance_result("jam", token="t"))
    assert info.value.code is None


def test_default_construction_keeps_existing_callers_working():
    e = BedUnavailable("plain message")
    assert str(e) == "plain message"
    assert e.code is None
