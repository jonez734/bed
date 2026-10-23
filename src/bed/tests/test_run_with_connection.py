"""run_with_connection runs a bed coroutine on one loop and closes the connection on that loop.

It replaces `asyncio.run(...)` at the one-shot call sites. The socket is closed even when the
call raises, and the close runs on the same loop as the call.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from bed.client.connection import BedConnection, run_with_connection


class FakeWS:
    def __init__(self):
        self.closed_calls = 0
        self.close_loop = None

    async def close(self):
        self.closed_calls += 1
        self.close_loop = asyncio.get_running_loop()


def _conn_with_socket():
    args = MagicMock(bed_host="127.0.0.1", bed_port=8765, bed_path="/", bed_call_timeout=5.0)
    c = BedConnection(args)
    c._ws = FakeWS()
    return c


@pytest.mark.unit
def test_returns_the_coroutine_result():
    c = _conn_with_socket()

    async def call():
        return {"ok": True}

    assert run_with_connection(c, call) == {"ok": True}


@pytest.mark.unit
def test_closes_the_socket_after_the_call():
    c = _conn_with_socket()
    ws = c._ws

    async def call():
        return 1

    run_with_connection(c, call)
    assert ws.closed_calls == 1
    assert c._ws is None


@pytest.mark.unit
def test_closes_the_socket_when_the_call_raises():
    c = _conn_with_socket()
    ws = c._ws

    async def call():
        raise ConnectionError("gone")

    with pytest.raises(ConnectionError):
        run_with_connection(c, call)
    assert ws.closed_calls == 1


@pytest.mark.unit
def test_close_runs_on_the_loop_of_the_call():
    c = _conn_with_socket()
    ws = c._ws
    call_loop = {}

    async def call():
        call_loop["loop"] = asyncio.get_running_loop()
        return 1

    run_with_connection(c, call)
    assert ws.close_loop is call_loop["loop"]


@pytest.mark.unit
def test_no_task_is_left_running_after_the_call():
    c = _conn_with_socket()
    seen = {}

    async def call():
        seen["loop"] = asyncio.get_running_loop()
        return 1

    run_with_connection(c, call)
    loop = seen["loop"]
    assert loop.is_closed(), "the loop used for the call must be closed"
