"""Single shared WebSocket connection to a bed daemon.

A :class:`BedConnection` is the only thing in the bed client that
talks to ``websockets``. It is opened lazily on the first
:meth:`send` call. A single connection is held for the lifetime of
the Python process (or until :meth:`force_close` is invoked, e.g.
from a test).

Threading: ``send`` is async and uses an :class:`asyncio.Lock` so
multiple coroutines on the same loop serialise their writes.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, Optional

from bbsengine6 import io

from bed.client.exceptions import BedUnavailable

try:
    import websockets
    from websockets.exceptions import WebSocketException
except ImportError as _exc:
    raise ImportError(
        "bed.client requires the 'websockets' package. "
        "Install it via `pip install websockets`."
    ) from _exc


class _RequestId:
    """Monotonic request id counter, scoped per connection."""

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            self._n += 1
            return f"r{self._n}"


class BedConnection:
    """Single shared WebSocket connection to a bed daemon."""

    def __init__(self, args: Any) -> None:
        self._args = args
        self._ws: Optional[Any] = None
        self._lock: Optional[asyncio.Lock] = None
        self._request_ids = _RequestId()

    @property
    def host(self) -> str:
        return getattr(self._args, "bed_host", "localhost")

    @property
    def port(self) -> int:
        return int(getattr(self._args, "bed_port", 8765))

    @property
    def path(self) -> str:
        return getattr(self._args, "bed_path", "/")

    @property
    def call_timeout(self) -> float:
        return float(getattr(self._args, "bed_call_timeout", 5.0))

    def _ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"

    async def _connect(self) -> Any:
        try:
            return await asyncio.wait_for(
                websockets.connect(self._ws_url()),
                timeout=self.call_timeout,
            )
        except (
            ConnectionRefusedError,
            OSError,
            asyncio.TimeoutError,
            WebSocketException,
        ) as exc:
            raise BedUnavailable(
                f"cannot connect to {self._ws_url()}: {exc}"
            ) from exc

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _is_closed(self) -> bool:
        ws = self._ws
        if ws is None:
            return True
        closed = getattr(ws, "closed", None)
        if closed is not None:
            return bool(closed)
        state = getattr(ws, "state", None)
        if state is not None:
            return str(state).split(".")[-1] not in ("OPEN", "CONNECTING")
        return False

    async def send(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Send ``message`` (dict) to bed and await the reply.

        Awaits a reply whose ``type`` ends with ``_result`` and whose
        ``request_id`` matches the one we injected (if we injected
        one). For ``ping`` requests the reply is matched on ``type``
        ``pong``.

        Raises :class:`BedUnavailable` on any transport failure.
        """
        if "request_id" not in message:
            message = dict(message)
            message["request_id"] = self._request_ids.next()

        request_id = message["request_id"]

        async with self._get_lock():
            try:
                if self._ws is None or self._is_closed():
                    self._ws = await self._connect()
                ws = self._ws
                if ws is None:
                    raise BedUnavailable("bed connection is None after connect")
                await ws.send(json.dumps(message))
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=self.call_timeout
                )
            except (
                ConnectionRefusedError,
                OSError,
                asyncio.TimeoutError,
                WebSocketException,
            ) as exc:
                self._ws = None
                raise BedUnavailable(
                    f"bed send/receive failed: {exc}"
                ) from exc

        try:
            reply = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise BedUnavailable(f"bed reply was not JSON: {exc}") from exc

        if request_id and reply.get("request_id") != request_id:
            io.echo(
                f"bed.client: mismatched request_id "
                f"sent={request_id} got={reply.get('request_id')!r}",
                level="warning",
            )
        return reply

    def force_close(self) -> None:
        """Close the current connection so the next send redials.

        Used by tests to simulate ``bed went down`` between calls. If
        the underlying websocket's ``close()`` is async, we schedule
        the close on a fresh event loop in a daemon thread.
        """
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            close = getattr(ws, "close", None)
            if close is None:
                return
            result = close()
            if asyncio.iscoroutine(result):

                def _runner(coro):
                    try:
                        loop = asyncio.new_event_loop()
                        try:
                            loop.run_until_complete(coro)
                        finally:
                            loop.close()
                    except Exception as e:
                        io.echo(
                            f"bed.client: force_close error: {e}",
                            level="debug",
                        )

                threading.Thread(target=_runner, args=(result,)).start()
        except Exception as e:
            io.echo(
                f"bed.client: force_close error: {e}",
                level="debug",
            )


def _expected_result_type(request_type: str) -> Optional[str]:
    """Map a request type to the conventional result type.

    ``ping`` → ``pong`` (special case). Otherwise: ``foo_bar`` → ``foo_bar_result``.
    """
    if not request_type:
        return None
    if request_type == "ping":
        return "pong"
    if request_type.endswith("_result"):
        return request_type
    return f"{request_type}_result"
