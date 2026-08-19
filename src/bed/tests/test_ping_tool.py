"""Tests for :mod:`bed.tools.ping` (the standalone ``bedping`` CLI script).

Covers:

- :class:`PingUnavailable` carries host/port and renders a one-line
  message that names the endpoint and hints at the likely cause.
- :func:`main` converts ``ConnectionRefusedError``, ``OSError``,
  ``asyncio.TimeoutError`` and ``WebSocketException`` into
  :class:`PingUnavailable` (no Python traceback escapes).
- :func:`main` calls :func:`bbsengine6.io.echo` with ``level="error"``
  and returns ``1`` on connection failure so the ``bin/bedping`` shim
  exits non-zero.
- :func:`main` returns ``0`` on the happy path (the existing
  ping/auth round-trip is preserved).
"""

from __future__ import annotations

import json
import os
import socket
import sys
from contextlib import contextmanager
from typing import Any, Iterator, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "/home/opencode/data/work/bed/src")
sys.path.insert(0, "/home/opencode/data/work/bbsengine6/py/src")


from bed.tools import ping as ping_tool  # noqa: E402


# ---------------------------------------------------------------------
# Helpers


def _free_port() -> int:
    """Bind an ephemeral IPv4 port and immediately release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _argv(*args: str) -> Iterator[None]:
    """Run a block with ``sys.argv`` replaced by ``['bedping', *args]``."""
    saved = sys.argv
    sys.argv = ["bedping", *args]
    try:
        yield
    finally:
        sys.argv = saved


class _FakeWebSocket:
    """Minimal async context manager stand-in for ``websockets`` protocol.

    Mirrors the shape ``bed.tools.ping._ping_then_auth`` uses after
    the refactor: ``bed.tools.ping.connect`` returns the live WS
    directly via ``await websockets.connect(url)`` (the helper
    awaits ``connect`` then ``send``/``recv`` on the result).
    """

    def __init__(self, frames: List[str]) -> None:
        self._frames = list(frames)
        self.sent: List[str] = []

    async def __aenter__(self) -> "_FakeWebSocket":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        return self._frames.pop(0)

    async def close(self) -> None:
        return None


def _connect_mock(fake: _FakeWebSocket) -> MagicMock:
    """Build a mock for ``websockets.connect(url)`` whose side_effect is
    an async function returning ``fake``. The shared helper awaits
    ``websockets.connect`` (not the legacy ``async with`` shape)."""

    async def _fake_connect(url: str, **kwargs: Any) -> _FakeWebSocket:
        return fake

    return MagicMock(side_effect=_fake_connect)


# ---------------------------------------------------------------------
# PingUnavailable


class TestPingUnavailableMessage:
    """The exception message names the endpoint and hints at the cause."""

    def test_message_includes_ws_url(self):
        exc = ping_tool.PingUnavailable(
            "localhost", 8765, ConnectionRefusedError("refused")
        )
        assert "ws://localhost:8765/" in str(exc)

    def test_message_includes_running_hint(self):
        exc = ping_tool.PingUnavailable(
            "localhost", 8765, ConnectionRefusedError("refused")
        )
        assert "is the bed daemon running?" in str(exc)

    def test_message_includes_original_exception(self):
        exc = ping_tool.PingUnavailable(
            "localhost", 8765, ConnectionRefusedError("nope")
        )
        assert "nope" in str(exc)

    def test_message_includes_bedping_prefix(self):
        exc = ping_tool.PingUnavailable(
            "h", 9, OSError("boom"), prog="bedping"
        )
        assert str(exc).startswith("bedping:")

    def test_attributes_carry_host_and_port(self):
        exc = ping_tool.PingUnavailable("h", 9, OSError("boom"))
        assert exc.host == "h"
        assert exc.port == 9


# ---------------------------------------------------------------------
# main() -> exit code 1 on connection failure


class TestMainConnectionRefused:
    """When ``websockets.connect`` raises ConnectionRefusedError,
    :func:`main` prints the friendly message via ``io.echo(level="error")``
    and returns ``1``.
    """

    def test_returns_one(self):
        ws = _FakeWebSocket([])
        from bbsengine6.net import ping as _ping_helper
        with patch.object(
            _ping_helper, "websockets"
        ) as ws_mod, patch.object(
            ping_tool.io, "echo"
        ) as echo:
            ws_mod.connect = MagicMock(
                side_effect=ConnectionRefusedError(
                    "[Errno 111] Connection refused"
                )
            )
            with _argv():
                rc = ping_tool.main()
        assert rc == 1
        echo.assert_called_once()
        msg, kwargs = echo.call_args
        assert "ws://localhost:8765/" in msg[0]
        assert "is the bed daemon running?" in msg[0]
        assert kwargs.get("level") == "error"

    def test_custom_host_and_port_in_message(self):
        from bbsengine6.net import ping as _ping_helper
        with patch.object(
            _ping_helper, "websockets"
        ) as ws_mod, patch.object(
            ping_tool.io, "echo"
        ) as echo:
            ws_mod.connect = MagicMock(
                side_effect=ConnectionRefusedError("refused")
            )
            with _argv("--host", "bed.internal", "--port", "9999"):
                rc = ping_tool.main()
        assert rc == 1
        msg = echo.call_args[0][0]
        assert "ws://bed.internal:9999/" in msg

    def test_no_traceback_escapes(self):
        """The connection error must be converted to PingUnavailable so
        no traceback reaches stderr."""
        from bbsengine6.net import ping as _ping_helper
        with patch.object(
            _ping_helper, "websockets"
        ) as ws_mod, patch.object(
            ping_tool.io, "echo"
        ):
            ws_mod.connect = MagicMock(
                side_effect=ConnectionRefusedError("refused")
            )
            with _argv():
                # No exception should propagate past main().
                rc = ping_tool.main()
        assert rc == 1


class TestMainOtherTransportErrors:
    """OSError, asyncio.TimeoutError and websockets WebSocketException
    must also be converted to PingUnavailable (the broad catch is
    intentional)."""

    def test_oserror_is_friendly(self):
        from bbsengine6.net import ping as _ping_helper
        with patch.object(
            _ping_helper, "websockets"
        ) as ws_mod, patch.object(
            ping_tool.io, "echo"
        ) as echo:
            ws_mod.connect = MagicMock(
                side_effect=OSError("host unreachable")
            )
            with _argv("--host", "nope.example"):
                rc = ping_tool.main()
        assert rc == 1
        assert "host unreachable" in echo.call_args[0][0]
        assert "ws://nope.example:8765/" in echo.call_args[0][0]

    def test_timeouterror_is_friendly(self):
        from bbsengine6.net import ping as _ping_helper
        with patch.object(
            _ping_helper, "websockets"
        ) as ws_mod, patch.object(
            ping_tool.io, "echo"
        ) as echo:
            ws_mod.connect = MagicMock(
                side_effect=TimeoutError("slow")
            )
            with _argv():
                rc = ping_tool.main()
        assert rc == 1
        assert "slow" in echo.call_args[0][0]

    def test_websocket_exception_is_friendly(self):
        from websockets.exceptions import WebSocketException
        from bbsengine6.net import ping as _ping_helper
        with patch.object(
            _ping_helper, "websockets"
        ) as ws_mod, patch.object(
            ping_tool.io, "echo"
        ) as echo:
            ws_mod.connect = MagicMock(
                side_effect=WebSocketException("bad handshake")
            )
            with _argv():
                rc = ping_tool.main()
        assert rc == 1
        assert "bad handshake" in echo.call_args[0][0] 


# ---------------------------------------------------------------------
# Happy path regression guard


class TestMainHappyPath:
    """When the server returns a valid pong, ``main`` completes the
    ping/auth round-trip and returns ``0``."""

    def test_ping_auth_round_trip_returns_zero(self, monkeypatch):
        port = _free_port()

        # Stub input() so the script doesn't block on stdin.
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "alice")

        # The fake WebSocket replies to ``ping`` with a pong and to
        # ``auth`` with a success envelope.
        fake = _FakeWebSocket([
            json.dumps({"type": "pong", "name": "bed", "version": "0.0.0"}),
            json.dumps({"type": "auth_result", "ok": True}),
        ])

        from bbsengine6.net import ping as _ping_helper
        with patch.object(_ping_helper, "websockets") as ws_mod, \
             patch.object(ping_tool.io, "echo"), \
             patch("builtins.print") as print_:
            ws_mod.connect = _connect_mock(fake)
            with _argv("--host", "127.0.0.1", "--port", str(port)):
                rc = ping_tool.main()

        assert rc == 0
        # The script sent a ping and an auth frame, in that order.
        assert len(fake.sent) == 2
        assert json.loads(fake.sent[0])["type"] == "ping"
        assert json.loads(fake.sent[1])["type"] == "auth"

    def test_invalid_pong_is_not_silenced(self, monkeypatch):
        """A pong-shaped mistake from the server (not a transport error)
        must still raise — we only swallow connection failures, not
        protocol-level bugs."""
        port = _free_port()
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "alice")

        # Server replies with a wrong type. The script asserts on
        # ``type == "pong"``; that AssertionError must propagate so it
        # is visible to the operator, not silently swallowed into the
        # "connection refused" branch.
        fake = _FakeWebSocket([json.dumps({"type": "wat"})])

        from bbsengine6.net import ping as _ping_helper
        with patch.object(_ping_helper, "websockets") as ws_mod, \
             patch.object(ping_tool.io, "echo"):
            ws_mod.connect = _connect_mock(fake)
            with _argv("--host", "127.0.0.1", "--port", str(port)):
                with pytest.raises(AssertionError):
                    ping_tool.main()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
