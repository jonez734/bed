"""`message watch` on a real BedConnection: Ctrl-C leaves no receive task and no open socket.

The connection is the real class, so subscribe starts the real receive task. Only the websocket is
faked, and it records its close. Ctrl-C ends the tail, and run_with_connection closes the connection.
"""

import asyncio
import gc
import warnings
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bed.client.connection import BedConnection
from bed.tools import message as msg_tool


class _Ws:
    def __init__(self):
        self.closes = 0

    async def recv(self):
        await asyncio.sleep(3600)

    async def close(self):
        self.closes += 1


def _args():
    return SimpleNamespace(debug=False, token_file="/tmp/none.token", bed_host="127.0.0.1",
                           bed_port=8765, bed_path="/", bed_call_timeout=5.0, bed_probe_timeout=0.25,
                           moniker="alice", sysop=False, direct=False, _backend="bed", databasename="zoid6")


@pytest.mark.unit
def test_ctrl_c_on_watch_leaves_no_receive_task_and_no_open_socket():
    ws = _Ws()
    real = BedConnection(_args())
    seen = {}

    async def connect():
        return ws

    real._connect = connect
    ticks = {"n": 0}

    async def interrupt_after_subscribe(_):
        # The tail is running: subscribe has started the real receive task. Capture it, then Ctrl-C.
        ticks["n"] += 1
        seen["task"] = real._recv_task
        raise KeyboardInterrupt()

    class Facade:
        def __init__(self, args):
            self._conn = real

        def subscribe(self, moniker):
            return {"ok": True}

        def unsubscribe(self, moniker):
            return {"ok": True}

    with warnings.catch_warnings(record=True) as rec, \
         patch.object(msg_tool, "_check_access", return_value=True), \
         patch.object(msg_tool, "_BedMessageFacade", Facade), \
         patch.object(msg_tool.asyncio, "sleep", side_effect=interrupt_after_subscribe), \
         patch.object(msg_tool.io, "echo"):
        warnings.simplefilter("always")
        result = msg_tool.message_watch(_args(), "alice")
        gc.collect()

    assert result is True
    assert ws.closes == 1, "the websocket must be closed"
    assert seen.get("task") is not None, "subscribe must have started a receive task"
    assert seen["task"].done(), "the receive task must be finished, not merely cancelled"
    assert real._ws is None
    assert [w for w in rec if issubclass(w.category, ResourceWarning)] == []
