#!/usr/bin/env python3
# bed/tests/test_connection_redial.py
# The recv loop redials when the socket drops and runs reconnect hooks.

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


def test_recv_loop_redials_and_runs_hooks_after_drop():
    async def run():
        conn = BedConnection(_args())
        dead = MagicMock(name="dead")
        dead.closed = True
        live = MagicMock(name="live")
        live.closed = False
        live.recv = AsyncMock(side_effect=asyncio.CancelledError())
        conn._ws = dead
        conn._recv_stop = asyncio.Event()
        hook = AsyncMock()
        conn.add_reconnect_hook(hook)
        with patch.object(conn, "_connect", AsyncMock(return_value=live)):
            task = asyncio.create_task(conn._recv_loop())
            for _ in range(50):
                if hook.await_count:
                    break
                await asyncio.sleep(0.01)
            conn._recv_stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return hook.await_count, conn._ws

    count, ws = asyncio.run(run())
    assert count >= 1
    assert ws is not None


def test_failed_redial_backs_off_without_hook():
    from bed.client.exceptions import BedUnavailable

    async def run():
        conn = BedConnection(_args())
        conn._ws = None
        conn._recv_stop = asyncio.Event()
        hook = AsyncMock()
        conn.add_reconnect_hook(hook)
        with patch.object(conn, "_connect", AsyncMock(side_effect=BedUnavailable("down"))):
            task = asyncio.create_task(conn._recv_loop())
            await asyncio.sleep(0.05)
            conn._recv_stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return hook.await_count

    assert asyncio.run(run()) == 0


def test_hook_registration_is_idempotent_and_removable():
    conn = BedConnection(_args())

    async def h():
        return None

    conn.add_reconnect_hook(h)
    conn.add_reconnect_hook(h)
    assert len(conn._reconnect_hooks) == 1
    conn.remove_reconnect_hook(h)
    assert conn._reconnect_hooks == []
