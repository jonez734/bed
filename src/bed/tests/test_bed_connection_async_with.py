"""BedConnection supports `async with`: the websocket is closed on the loop that opened it.

The close runs once, also when the body raises, and it is awaited (not scheduled on another
loop). A connection that never opened has nothing to close.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from bed.client.connection import BedConnection


class FakeWS:
    def __init__(self):
        self.closed_calls = 0
        self.close_loop = None
        self.closed = False

    async def close(self):
        self.closed_calls += 1
        self.close_loop = asyncio.get_running_loop()
        self.closed = True


def _conn():
    args = MagicMock(bed_host="127.0.0.1", bed_port=8765, bed_path="/", bed_call_timeout=5.0)
    return BedConnection(args)


@pytest.mark.unit
def test_aexit_closes_the_websocket_once():
    async def run():
        c = _conn()
        ws = FakeWS()
        c._ws = ws
        async with c:
            pass
        return ws
    ws = asyncio.run(run())
    assert ws.closed_calls == 1


@pytest.mark.unit
def test_aexit_closes_on_the_loop_that_opened_the_connection():
    async def run():
        c = _conn()
        ws = FakeWS()
        c._ws = ws
        opened_on = asyncio.get_running_loop()
        async with c:
            pass
        return ws, opened_on
    ws, opened_on = asyncio.run(run())
    assert ws.close_loop is opened_on


@pytest.mark.unit
def test_aexit_still_closes_when_the_body_raises():
    async def run():
        c = _conn()
        ws = FakeWS()
        c._ws = ws
        with pytest.raises(RuntimeError):
            async with c:
                raise RuntimeError("boom")
        return ws
    ws = asyncio.run(run())
    assert ws.closed_calls == 1


@pytest.mark.unit
def test_aenter_returns_the_connection():
    async def run():
        c = _conn()
        async with c as got:
            return got is c
    assert asyncio.run(run()) is True


@pytest.mark.unit
def test_aexit_with_no_socket_is_a_no_op():
    async def run():
        c = _conn()
        async with c:
            pass
        return c._ws
    assert asyncio.run(run()) is None


@pytest.mark.unit
def test_aexit_clears_the_socket_so_a_later_send_redials():
    async def run():
        c = _conn()
        c._ws = FakeWS()
        async with c:
            pass
        return c._ws
    assert asyncio.run(run()) is None
