"""`message watch` ends gracefully on Ctrl-C or EOF: the socket is closed, nothing is raised.

The tail loop is interrupted by simulating the operator's key press. The command must return
True, the connection must be closed on the loop that opened it, and no traceback or
ResourceWarning may be produced.
"""

import asyncio
import gc
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bed.tools import message as msg_tool


class WatchConn:
    def __init__(self):
        self.closes = 0
        self.close_loop = None
        self.open_loop = None
        self.subscribed = []

    async def subscribe(self, handler):
        self.open_loop = asyncio.get_running_loop()
        self.subscribed.append(handler)

    async def unsubscribe(self, handler):
        return None

    async def aclose(self):
        self.closes += 1
        self.close_loop = asyncio.get_running_loop()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
        return False

    async def send(self, msg):
        return {"ok": True, "moniker": "alice"}


def _args():
    return SimpleNamespace(debug=False, token_file="/tmp/none.token", bed_host="127.0.0.1",
                           bed_port=8765, bed_path="/", bed_call_timeout=5.0, bed_probe_timeout=0.25,
                           moniker="alice", sysop=False, direct=False, _backend="bed",
                           databasename="zoid6")


def _interrupt_after_first_tick(exc):
    """A sleep replacement: the first tick raises the key press (Ctrl-C or EOF)."""
    calls = {"n": 0}

    async def fake_sleep(_):
        calls["n"] += 1
        raise exc

    return fake_sleep


def _watch(exc, conn):
    with warnings.catch_warnings(record=True) as rec, \
         patch.object(msg_tool, "_check_access", return_value=True), \
         patch.object(msg_tool, "_BedMessageFacade") as facade, \
         patch.object(msg_tool.asyncio, "sleep", side_effect=_interrupt_after_first_tick(exc)), \
         patch.object(msg_tool.io, "echo"):
        warnings.simplefilter("always")
        facade.return_value._conn = conn
        facade.return_value.subscribe.return_value = {"ok": True}
        facade.return_value.unsubscribe.return_value = {"ok": True}
        result = msg_tool.message_watch(_args(), "alice")
        gc.collect()
    return result, [w for w in rec if issubclass(w.category, ResourceWarning)]


@pytest.mark.unit
def test_ctrl_c_returns_true_and_does_not_raise():
    conn = WatchConn()
    result, _ = _watch(KeyboardInterrupt(), conn)
    assert result is True


@pytest.mark.unit
def test_eof_returns_true_and_does_not_raise():
    conn = WatchConn()
    result, _ = _watch(EOFError(), conn)
    assert result is True


@pytest.mark.unit
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), EOFError()])
def test_watch_closes_the_connection_on_exit(exc):
    conn = WatchConn()
    _watch(exc, conn)
    assert conn.closes == 1


@pytest.mark.unit
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), EOFError()])
def test_watch_closes_on_the_loop_that_opened_the_connection(exc):
    conn = WatchConn()
    _watch(exc, conn)
    assert conn.close_loop is conn.open_loop


@pytest.mark.unit
@pytest.mark.parametrize("exc", [KeyboardInterrupt(), EOFError()])
def test_watch_leaves_no_resource_warning(exc):
    _, leaks = _watch(exc, WatchConn())
    assert leaks == [], [str(w.message) for w in leaks]
