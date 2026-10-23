"""A failed send closes the websocket it opened, instead of dropping it for garbage collection.

When the request or its reply fails, BedConnection.send must close the socket on the same loop
before it raises. Otherwise the socket is left unreferenced and reported as a ResourceWarning.
"""

import asyncio
import gc
import warnings
from unittest.mock import MagicMock

import pytest
import websockets

from bed.client.connection import BedConnection


class _Ws:
    def __init__(self, fail_send=False):
        self.fail_send = fail_send
        self.closes = 0
        self.close_loop = None

    async def send(self, data):
        if self.fail_send:
            raise ConnectionError("dropped mid-request")

    async def recv(self):
        raise ConnectionError("no reply")

    async def close(self):
        self.closes += 1
        self.close_loop = asyncio.get_running_loop()


def _conn(ws):
    args = MagicMock(bed_host="127.0.0.1", bed_port=8765, bed_path="/", bed_call_timeout=5.0)
    c = BedConnection(args)

    async def fake_connect():
        return ws

    c._connect = fake_connect
    return c


@pytest.mark.unit
def test_failed_send_closes_the_socket_it_opened():
    ws = _Ws(fail_send=True)
    c = _conn(ws)

    async def run():
        with pytest.raises(Exception):
            await c.send({"type": "bank_balance"})

    asyncio.run(run())
    assert ws.closes == 1


@pytest.mark.unit
def test_failed_send_closes_on_the_loop_that_opened_it():
    ws = _Ws(fail_send=True)
    c = _conn(ws)
    opened = {}

    async def run():
        opened["loop"] = asyncio.get_running_loop()
        with pytest.raises(Exception):
            await c.send({"type": "bank_balance"})

    asyncio.run(run())
    assert ws.close_loop is opened["loop"]


@pytest.mark.unit
def test_failed_send_leaves_no_resource_warning():
    ws = _Ws(fail_send=True)
    c = _conn(ws)
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")

        async def run():
            with pytest.raises(Exception):
                await c.send({"type": "bank_balance"})

        asyncio.run(run())
        gc.collect()
    assert [w for w in rec if issubclass(w.category, ResourceWarning)] == []
