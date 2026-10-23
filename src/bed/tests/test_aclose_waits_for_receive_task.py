"""aclose() waits for the receive task to finish before it returns.

A receive task that takes time to honour cancellation (for example, one that is inside a
reconnect hook) must still be finished when aclose() returns, so nothing it does can run after
the close.
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
def test_aclose_returns_only_after_a_slow_receive_task_has_finished():
    async def run():
        c = _conn()
        ws = _Ws()
        finished = {}

        async def slow_recv_loop():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                # Takes time to unwind, like a reconnect hook in progress.
                await asyncio.sleep(0.05)
                finished["at"] = True
                raise

        async def connect():
            return ws

        c._connect = connect
        c._ws = ws
        c._recv_stop = asyncio.Event()
        c._recv_task = asyncio.create_task(slow_recv_loop())
        await asyncio.sleep(0)
        await c.aclose()
        return finished, c._recv_task

    finished, task = asyncio.run(run())
    assert finished.get("at") is True, "aclose returned before the receive task finished"
    assert task is None or task.done()


@pytest.mark.unit
def test_aclose_with_no_receive_task_is_immediate():
    async def run():
        c = _conn()
        c._ws = _Ws()
        await c.aclose()
        return c._ws

    assert asyncio.run(run()) is None
