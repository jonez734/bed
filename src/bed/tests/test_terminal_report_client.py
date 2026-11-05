#!/usr/bin/env python3
# bed/tests/test_terminal_report_client.py
# The client reports its terminal to bed once per connection, when probe_terminal is on.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bed.client.connection import BedConnection

REPORT = {
    "term": "xterm-256color",
    "colors": 256,
    "true_color": True,
    "color_256": True,
    "source": "query",
    "ansi": "\x1b[?62c",
    "extra": "dropped-by-client",
}


def _args(probe=None):
    a = MagicMock()
    a.bed_host = "127.0.0.1"
    a.bed_port = 8765
    a.bed_path = "/"
    a.bed_call_timeout = 1.0
    if probe is not None:
        a.probe_terminal = probe
    return a


def test_probe_is_off_for_mock_args_without_an_explicit_bool():
    # A MagicMock attribute is truthy but is not a bool, so the flag stays off.
    assert BedConnection(_args())._probe_terminal is False
    assert BedConnection(_args(probe="yes"))._probe_terminal is False


def test_explicit_flag_wins_over_args():
    assert BedConnection(_args(probe=False), probe_terminal=True)._probe_terminal is True
    assert BedConnection(_args(probe=True), probe_terminal=False)._probe_terminal is False


def test_report_sends_only_the_known_fields():
    async def run():
        conn = BedConnection(_args(), probe_terminal=True)
        conn._ws = MagicMock()
        sent = AsyncMock(return_value={"type": "terminal_caps_ack", "stored": True})
        with patch("bbsengine6.io.terminal.terminal_report", return_value=REPORT), \
                patch.object(conn, "send", sent):
            await conn._report_terminal()
        msg = sent.await_args.args[0]
        assert msg["type"] == "terminal_caps"
        assert "extra" not in msg["caps"]
        assert msg["caps"]["source"] == "query"

    asyncio.run(run())


def test_no_report_when_flag_off():
    async def run():
        conn = BedConnection(_args(), probe_terminal=False)
        conn._ws = MagicMock()
        sent = AsyncMock()
        with patch.object(conn, "send", sent):
            await conn._report_terminal()
        sent.assert_not_awaited()

    asyncio.run(run())


def test_no_report_without_a_socket():
    async def run():
        conn = BedConnection(_args(), probe_terminal=True)
        sent = AsyncMock()
        with patch.object(conn, "send", sent):
            await conn._report_terminal()
        sent.assert_not_awaited()

    asyncio.run(run())


def test_failed_report_is_swallowed():
    async def run():
        conn = BedConnection(_args(), probe_terminal=True)
        conn._ws = MagicMock()
        with patch("bbsengine6.io.terminal.terminal_report", side_effect=OSError("no tty")):
            await conn._report_terminal()  # must not raise

    asyncio.run(run())


def test_refused_caps_are_logged_not_raised():
    async def run():
        conn = BedConnection(_args(), probe_terminal=True)
        conn._ws = MagicMock()
        sent = AsyncMock(return_value={"type": "terminal_caps_ack", "stored": False, "error": "bad"})
        # Patch echo so the warning does not depend on shared echo state.
        with patch("bbsengine6.io.terminal.terminal_report", return_value=REPORT), \
                patch.object(conn, "send", sent), \
                patch("bed.client.connection.io.echo") as echo:
            await conn._report_terminal()  # must not raise
        sent.assert_awaited_once()
        assert echo.call_args.kwargs.get("level") == "warning"

    asyncio.run(run())
