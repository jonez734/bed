"""Shared helpers for bed's auth integration tests.

This module is the importable sibling of :mod:`conftest`. Pytest's
package import mode (the test directory has ``__init__.py``) does
NOT make ``conftest`` importable from test files, so any class or
function a test wants to call goes here. Pytest fixtures stay in
:mod:`conftest`.

Mirrors the bank-integration pattern at
``test_bank_integration.py:125-190``:

- :class:`StubCredentialProvider` -- stub provider accepting
  ``("alice", "pw")`` and ``("root", "rootpw")`` with stable
  ``loginid`` so the cross-suite convention is consistent.
- :func:`_start_bed_with_auth` -- async helper returning the
  ``(server, port, session_registry, auth_service)`` 4-tuple used
  by every wire-level test in :mod:`test_auth_integration`.
- :class:`BedServerContext` -- sync ``__enter__/__exit__`` that
  drives the in-process bed server in a daemon thread with its
  own asyncio event loop. The thread keeps the loop running so
  the server can complete WebSocket handshakes when the test
  thread's :func:`asyncio.run` opens a fresh loop to drive the
  client. (Driving the server in a paused loop would cause
  handshake timeouts because the paused loop doesn't service
  the accept / handshake tasks.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import socket as _socket
import sys
import threading
from typing import Any, Dict, Optional


# Make ``bed.*`` importable when this module is imported outside
# the pytest collection path.
sys.path.insert(0, "/home/opencode/data/work/bed/src")


# ---------------------------------------------------------------------
# Constants


LIVE_HOST = "127.0.0.1"
LIVE_PORT = 8765


# ---------------------------------------------------------------------
# Helpers


class StubCredentialProvider:
    """Stub credential provider used by the in-process server.

    Accepts ``("alice", "pw")`` as a normal member and ``("root",
    "rootpw")`` as a sysop. Any other input returns ``None`` (which
    AuthService translates to ``bad_credentials``).
    """

    def authenticate(self, args, moniker, password, *, pool=None):
        from bed.api.token_store import MemberInfo

        if moniker == "alice" and password == "pw":
            return MemberInfo(
                moniker="alice",
                is_sysop=False,
                balance=7,
                loginid="alice_os",
            )
        if moniker == "root" and password == "rootpw":
            return MemberInfo(
                moniker="root",
                is_sysop=True,
                balance=0,
                loginid="root_os",
            )
        return None


def _auth_args() -> argparse.Namespace:
    """Stand-in ``argparse.Namespace`` for ``bbsengine6.auth.access``."""
    return argparse.Namespace(debug=False, pool=None)


# Test timeout budget. On localhost every bed round-trip completes
# in well under 100 ms; any individual harness wait that exceeds a
# few hundred ms indicates a hang (e.g. the broken
# ``WebSocketServer.stop()`` graceful shutdown we used to hit,
# which blocked on a never-arriving client close frame). Keep
# these tight so a regression trips the test in under a second
# instead of after the default 10-second ``Future.result`` /
# ``Thread.join`` timeout.
SERVER_START_TIMEOUT = 2.0
SHUTDOWN_TIMEOUT = 2.0
THREAD_JOIN_TIMEOUT = 2.0
WS_RECV_TIMEOUT = 0.5
BED_CALL_TIMEOUT = 0.5
BED_PROBE_TIMEOUT = 0.25


async def _start_bed_with_auth(
    *,
    instance_id: str = "auth-integration-test",
    secret: Optional[bytes] = None,
    ttl_seconds: int = 900,
    credential_provider: Any = None,
    clock: Any = None,
):
    """Spin up a WebSocketServer with ``AuthService`` registered.

    Returns ``(server, port, session_registry, auth_service)`` --
    the 4-tuple every wire-level test in
    :mod:`test_auth_integration` already destructures. Mirrors the
    bind-then-read ephemeral-port trick at
    ``test_bank_integration.py:181-185``.
    """
    from bbsengine6.net import WebSocketServer
    from bed.api import AuthService, InMemoryTokenStore
    from bed.api.session import SessionRegistry

    if credential_provider is None:
        credential_provider = StubCredentialProvider()
    token_store = InMemoryTokenStore()
    registry = SessionRegistry()
    auth_service = AuthService(
        args=_auth_args(),
        session_registry=registry,
        token_store=token_store,
        credential_provider=credential_provider,
        secret=secret if secret is not None else secrets.token_bytes(32),
        instance_id=instance_id,
        ttl_seconds=ttl_seconds,
        clock=clock,
    )

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = WebSocketServer(host="127.0.0.1", port=port)
    auth_service.register_all(server)
    await server.start()
    return server, port, registry, auth_service


async def _send_and_recv(
    ws: Any, payload: Dict[str, Any], *, timeout: float = WS_RECV_TIMEOUT
) -> Dict[str, Any]:
    """JSON round-trip over a raw ``websockets`` connection."""
    await ws.send(json.dumps(payload))
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


def _live_daemon_reachable() -> bool:
    """True iff a TCP connect to ``LIVE_HOST:LIVE_PORT`` succeeds."""
    try:
        with _socket.create_connection((LIVE_HOST, LIVE_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _drop_bed_connection_singleton(args: Any) -> None:
    """Drop the cached :class:`BedConnection` for ``args`` and close
    its underlying websocket socket directly.

    The production ``bed.client.singleton.reset_bed_connection``
    calls ``force_close()`` to "close the current connection so the
    next send redials". ``force_close`` is a sync method that
    spawns a daemon thread driving a fresh event loop to run the
    websocket's async ``close()``. The websocket's internal state
    (e.g. ``connection_lost_waiter`` futures) is bound to the
    loop on which the websocket was originally created. In tests
    each tool function (``auth_login`` etc.) closes that loop on
    return, so when ``force_close``'s daemon thread later runs
    ``run_until_complete`` the websocket accesses futures bound
    to the (now closed) original loop and raises
    ``RuntimeError: ... attached to a different loop``.

    We do something gentler: synchronously close the underlying
    ``socket.socket`` and detach it from the websocket's transport
    so the transport's destructor does not emit a
    ``ResourceWarning: unclosed transport``. This is only safe
    because the loop that owned the websocket is already closed
    (so no reader/writer callbacks will fire) and the websocket
    is about to be GC'd anyway.
    """
    from bed.client import singleton

    with singleton._CONNECTION_SINGLETON_LOCK:
        entry = singleton._CONNECTION_SINGLETON.pop(id(args), None)
    if entry is None:
        return
    _ref, conn = entry
    ws = getattr(conn, "_ws", None)
    if ws is None:
        return
    try:
        transport = getattr(ws, "transport", None)
        if transport is None:
            return
        # Synchronously close the underlying socket fd and detach
        # it from the transport so its ``__del__`` does not warn.
        sock = getattr(transport, "_sock", None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        try:
            transport._sock = None
        except Exception:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------
# Sync context manager for unittest.TestCase callers


class BedServerContext:
    """Sync context manager that runs an in-process bed server in a
    background thread with its own asyncio event loop.

    The thread is required because the bed test pattern is: the
    test thread calls ``asyncio.run(client_func())`` to drive the
    client, while the server must keep accepting connections and
    completing WebSocket handshakes. Both share a single TCP port
    but need independent event loops (a paused asyncio loop won't
    service its tasks).

    On exit the harness:

    1. Cancels every pending task on the server loop except the
       shutdown coroutine itself (``conn_handler``,
       ``Connection.keepalive``, ``Server._close``).
    2. Awaits the cancelled tasks so they unwind before the loop
       is closed (this is what eliminates the "Task was destroyed
       but it is pending!" warnings).
    3. Stops the loop, joins the thread, then closes the loop.

    We deliberately do NOT call ``WebSocketServer.stop()``: that
    awaits ``self._server.wait_closed()``, which waits for every
    client connection's close-handshake to complete. In the test
    scenario the client side has already torn down its loop (each
    ``auth_*`` call uses ``asyncio.run``), so the close handshake
    never happens and ``stop()`` hangs forever. Cancelling every
    loop task instead makes the websockets library unwind its
    internal ``_close`` / ``conn_handler`` / ``keepalive`` tasks
    cleanly via :class:`asyncio.CancelledError`, which is the
    supported way to abort an asyncio server.
    """

    def __init__(
        self,
        *,
        instance_id: str = "auth-tool-integration-test",
        ttl_seconds: int = 900,
        secret: Optional[bytes] = None,
        credential_provider: Any = None,
    ) -> None:
        self.instance_id = instance_id
        self.ttl_seconds = ttl_seconds
        self.secret = secret
        self.credential_provider = credential_provider
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self.server: Any = None
        self.port: Optional[int] = None
        self.auth_service: Any = None
        self.session_registry: Any = None
        self.token_store: Any = None

    def __enter__(self) -> "BedServerContext":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="bed-test-server"
        )
        self._thread.start()
        try:
            future = asyncio.run_coroutine_threadsafe(
                _start_bed_with_auth(
                    instance_id=self.instance_id,
                    ttl_seconds=self.ttl_seconds,
                    secret=self.secret,
                    credential_provider=self.credential_provider,
                ),
                self._loop,
            )
            (
                self.server,
                self.port,
                self.session_registry,
                self.auth_service,
            ) = future.result(timeout=SERVER_START_TIMEOUT)
            self.token_store = self.auth_service.token_store
        except BaseException:
            self._safe_shutdown()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._safe_shutdown()

    def _safe_shutdown(self) -> None:
        if self._loop is None:
            return
        try:
            if self.server is not None:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._shutdown_server(), self._loop
                    )
                    future.result(timeout=SHUTDOWN_TIMEOUT)
                except BaseException:
                    pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass
            if self._thread is not None:
                self._thread.join(timeout=THREAD_JOIN_TIMEOUT)
        finally:
            try:
                self._loop.close()
            finally:
                self._loop = None
                self._thread = None

    async def _shutdown_server(self) -> None:
        """Cancel every task on the server loop (except this one),
        then await the cancellations so they unwind cleanly before
        the loop is closed.

        We deliberately skip :meth:`WebSocketServer.stop`. That
        method awaits ``self._server.wait_closed()``, which in
        turn waits for every open client connection's close
        handshake to complete. In the test scenario each
        :func:`bed.tools.auth.auth_*` call uses :func:`asyncio.run`
        to drive its client coroutine, which tears down its loop
        on return. The websockets connection on the server side
        therefore never sees a close frame from the peer, so
        ``stop()`` would block forever. Cancelling every task on
        the loop (the websockets ``Server._close``, every
        ``conn_handler`` and ``Connection.keepalive`` /
        ``Connection.close`` task) instead makes the library
        unwind via :class:`asyncio.CancelledError`, which is the
        supported way to abort an asyncio server.

        Before cancelling we synchronously close the underlying
        :class:`asyncio.Server` and every client transport so the
        selector's interest in the underlying fds is cleared and
        ``_call_connection_lost`` is scheduled. One ``await
        asyncio.sleep(0)`` lets those callbacks run, so the
        socket fds are actually closed before the loop is torn
        down -- otherwise we get ``ResourceWarning: unclosed
        socket`` warnings.
        """
        if self.server is None:
            return
        # Drop the reference so any re-entry sees a closed server.
        server = self.server
        self.server = None
        ws_server = getattr(server, "_server", None)
        if ws_server is not None:
            # Close the underlying asyncio.Server (closes the
            # listening socket and stops accepting new
            # connections). This is the sync half of
            # ``asyncio.Server.close``; ``wait_closed`` is what
            # would hang, so we skip it.
            try:
                asyncio_server = getattr(ws_server, "server", None)
                if asyncio_server is not None:
                    asyncio_server.close()
            except Exception:
                pass
            # Close every open client transport synchronously.
            # ``Transport.close`` removes the fd from the loop's
            # selector and schedules ``_call_connection_lost``;
            # one tick later the underlying socket is closed.
            try:
                connections = list(ws_server.connections)
            except Exception:
                connections = []
            for conn in connections:
                try:
                    transport = getattr(conn, "transport", None)
                    if transport is not None:
                        transport.close()
                except Exception:
                    pass
            # Also close any connections that are still in the
            # ``handlers`` dict but have already begun closing.
            try:
                for conn in list(getattr(ws_server, "handlers", {}).keys()):
                    if conn in connections:
                        continue
                    try:
                        transport = getattr(conn, "transport", None)
                        if transport is not None:
                            transport.close()
                    except Exception:
                        pass
            except Exception:
                pass
        # One loop tick so the scheduled ``_call_connection_lost``
        # callbacks actually run and the socket fds get closed.
        await asyncio.sleep(0)
        # Snapshot every task that is not this one. ``asyncio.all_tasks()``
        # with no argument returns only tasks bound to the current
        # (server) loop.
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not current]
        for t in pending:
            t.cancel()
        # Drain the cancelled tasks. ``return_exceptions=True``
        # swallows the :class:`asyncio.CancelledError` raised by each.
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Drop the server object last so the websockets internals
        # see no further reference from this harness.
        del server
