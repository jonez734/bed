"""aclose() ends the receive task and leaves no socket open, even when the task is mid-redial.

The receive task redials on its own when the socket drops. Closing must wait for that task to
finish, and a socket the task opens during the close must be closed too.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from bed.client.connection import BedConnection


class _Ws:
    def __init__(self):
        self.closes = 0

    async def recv(self):
        await asyncio.sleep(3600)

    async def close(self):
        self.closes += 1


def _conn():
    args = MagicMock(bed_host="127.0.0.1", bed_port=8765, bed_path="/", bed_call_timeout=5.0)
    return BedConnection(args)


@pytest.mark.unit
def test_aclose_finishes_the_receive_task():
    async def run():
        c = _conn()
        ws = _Ws()

        async def connect():
            return ws

        c._connect = connect
        c._ws = ws
        await c._ensure_recv_loop()
        task = c._recv_task
        await c.aclose()
        return task

    task = asyncio.run(run())
    assert task.done(), "aclose must wait for the receive task to finish"


@pytest.mark.unit
def test_aclose_leaves_no_open_socket():
    async def run():
        c = _conn()
        ws = _Ws()

        async def connect():
            return ws

        c._connect = connect
        c._ws = ws
        await c._ensure_recv_loop()
        await c.aclose()
        return c._ws, ws

    current, ws = asyncio.run(run())
    assert current is None
    assert ws.closes == 1


@pytest.mark.unit
def test_socket_dialed_after_a_stop_is_closed_not_kept():
    # The stop flag is set while a redial is in flight, and the dial then returns normally (as a
    # handshake does). The loop must close the socket it dialed, not keep it as the connection.
    async def run():
        c = _conn()
        dial_started = asyncio.Event()
        release_dial = asyncio.Event()
        made = []

        async def connect_then_return():
            dial_started.set()
            await release_dial.wait()
            ws = _Ws()
            made.append(ws)
            return ws

        c._connect = connect_then_return
        c._ws = None
        c._recv_stop = asyncio.Event()
        loop_task = asyncio.create_task(c._recv_loop())
        await dial_started.wait()
        c._recv_stop.set()
        release_dial.set()
        await loop_task
        return c._ws, made

    current, made = asyncio.run(run())
    assert current is None, "a socket dialed after a stop must not be kept"
    assert len(made) == 1 and made[0].closes == 1, "the dialed socket must be closed"
