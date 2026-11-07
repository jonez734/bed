#!/usr/bin/env python3
# bed/tests/test_recv_loop_closes_dropped_socket.py
# A socket the recv loop drops (recv error, or a redial replacing it) must be closed, not left
# for garbage collection. An open websocket left behind shows up as ResourceWarning and as
# "Task was destroyed but it is pending" from websockets' keepalive task.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bed.client.connection import BedConnection


def _args():
    a = MagicMock()
    a.bed_host = "127.0.0.1"
    a.bed_port = 8765
    a.bed_path = "/"
    a.bed_call_timeout = 1.0
    return a


def _socket(name, *, recv=None):
    ws = MagicMock(name=name)
    ws.closed = False
    ws.close = AsyncMock()
    ws.recv = recv if recv is not None else AsyncMock(side_effect=OSError("reset"))
    return ws


def test_recv_error_closes_the_dropped_socket():
    async def run():
        conn = BedConnection(_args())
        broken = _socket("broken")
        conn._ws = broken
        conn._recv_stop = asyncio.Event()
        # Stop after the first drop: no redial, so only the recv-error path runs.
        with patch.object(conn, "_connect", AsyncMock(side_effect=asyncio.CancelledError())):
            task = asyncio.create_task(conn._recv_loop())
            for _ in range(50):
                if broken.close.await_count:
                    break
                await asyncio.sleep(0.01)
            conn._recv_stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return broken.close.await_count

    assert asyncio.run(run()) == 1


def test_redial_closes_the_socket_it_replaces():
    async def run():
        conn = BedConnection(_args())
        old = _socket("old")
        new = _socket("new", recv=AsyncMock(side_effect=asyncio.CancelledError()))
        conn._ws = None
        conn._recv_stop = asyncio.Event()
        # First dial hands back a socket that then fails; a second dial returns `new`.
        dials = [old, new]

        async def fake_connect():
            return dials.pop(0)

        with patch.object(conn, "_connect", side_effect=fake_connect):
            task = asyncio.create_task(conn._recv_loop())
            for _ in range(100):
                if new.recv.await_count:
                    break
                await asyncio.sleep(0.01)
            conn._recv_stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return old.close.await_count

    assert asyncio.run(run()) == 1


def test_stop_recv_loop_closes_the_live_socket():
    async def run():
        conn = BedConnection(_args())
        live = _socket("live")
        conn._ws = live
        conn._recv_stop = asyncio.Event()
        conn._recv_task = asyncio.create_task(asyncio.sleep(3600))
        await conn.aclose()
        return live.close.await_count, conn._ws

    closes, ws = asyncio.run(run())
    assert closes == 1
    assert ws is None
