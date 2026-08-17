"""Regression test for the router-wiring fix in ``bed.main.BED.start``.

The bug being guarded against: ``BED.start`` was constructing the
:class:`MessageRouter` with only ``db_args``, leaving
``session_registry``, ``secret``, ``token_store``, and
``instance_id`` unset. The casino router fell back to a fresh
in-process ``CasinoSessionManager`` and zero token wiring, so its
per-op ``_check_access`` gate could not find the session
:class:`bed.api.auth.AuthService` had just bound. Every gameplay op
after a successful ``auth`` returned ``not_authenticated`` even
though the client was already logged in.

This test pins the wiring: a router constructed through the same
``BED.start`` code path must receive the session registry and token
kwargs. The end-to-end version lives in
``casino.tests.test_casino_integration``; the unit-style version
here runs without a DB and without a real WebSocket so it stays
fast and stable.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import unittest
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch


_THIS_DIR = "/home/opencode/data/work/bed/src/bed/tests"
for _p in (
    "/home/opencode/data/work/bed/src",
    "/home/opencode/data/work/bbsengine6/py/src",
    "/home/opencode/data/work/casino/src",
):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class _CapturingRouter:
    """Stand-in for the casino ``MessageRouter`` that records the
    kwargs ``BED.start`` passes to its constructor.

    ``register_all`` is a no-op so the test does not need a real
    WebSocketServer; the assertions live on the captured kwargs.
    """

    last_init_kwargs: Dict[str, Any] = {}

    def __init__(self, args: Any, **kwargs: Any) -> None:
        # Record both the positional ``args`` and every keyword
        # argument so the test can assert exactly what ``BED.start``
        # forwarded.
        type(self).last_init_kwargs = {"args": args, "kwargs": kwargs}

    def register_all(self, server: Any) -> None:
        return None

    def unregister_session(self, session_id: int) -> None:
        return None


def _make_bed_args(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token_persistence: str = "memory",
) -> argparse.Namespace:
    """Bed args namespace with the minimum surface ``BED.start``
    reads. ``bed_secret=""`` + ``bed_name="bed-rw-test"`` make
    ``_start_auth`` resolve the secret path under a name that is
    unlikely to collide with a real install.
    """
    return argparse.Namespace(
        host=host,
        port=port,
        debug=False,
        databasename="x",
        databasehost="x",
        databaseport=0,
        databaseuser="x",
        databasepassword="x",
        bed_secret="",
        bed_name="bed-rw-test",
        token_ttl=900,
        token_persistence=token_persistence,
        credential_provider="password",
        bed_instance_id=None,
        config_file="/dev/null",
        no_message_service=True,
        no_bank_service=True,
    )


def _fake_pool() -> Any:
    """A pool stand-in whose ``connection()`` is a no-op context
    manager so ``BED.start`` does not need a live Postgres."""
    pool = MagicMock()

    class _CM:
        def __enter__(self_inner):
            return MagicMock()

        def __exit__(self_inner, *a):
            return False

    pool.connection.return_value = _CM()
    return pool


class TestBEDRouterWiring(unittest.IsolatedAsyncioTestCase):
    """``BED.start`` must forward ``session_registry`` + token wiring
    to the ``MessageRouter`` so per-op auth gates can find the
    session ``AuthService`` just bound.
    """

    async def _run_bed_start(
        self,
        *,
        token_persistence: str = "memory",
        router_class: Any = _CapturingRouter,
        secret_bytes: Optional[bytes] = None,
        instance_id: Optional[str] = None,
    ) -> _CapturingRouter:
        """Drive ``BED.start`` with auth enabled + the given router
        class. Returns the singleton instance so the caller can read
        ``last_init_kwargs`` off the class.
        """
        from bbsengine6.net import WebSocketServer
        from bed.main import BED

        bed_main_mod = sys.modules["bed.main"]

        args = _make_bed_args(token_persistence=token_persistence)

        # Reset the capture before the run so a previous test's
        # kwargs do not leak into this assertion.
        _CapturingRouter.last_init_kwargs = {}

        # Mock DB pool so BED.start's "everything that can fail
        # BEFORE constructing the WebSocketServer" block does not
        # try to reach Postgres.
        #
        # Mock ``load_or_create_secret`` so ``_start_auth`` does not
        # touch ``~/.config/bed/...``. The real path would also
        # create / chmod files on the test machine, which would
        # leak state between runs.
        #
        # Spy on ``WebSocketServer.__init__`` so the server is
        # constructed but never started (no listener bound). This
        # is enough for BED.start to register services against it;
        # we tear it down via ``del`` + GC so no port is opened.
        fake_secret = secret_bytes or b"\x00" * 32
        fake_instance = instance_id or "router-wiring-test"

        class _NoStartServer:
            def __init__(self_inner, *a, **kw):
                pass

            def register_service(self_inner, *a, **kw):
                pass

            def list_services(self_inner):
                return {}

            async def start(self_inner):
                return None

            async def stop(self_inner):
                return None

        # ``bed.main`` transitively imports ``bbsengine6.io`` which
        # installs SIGINT/SIGTERM/SIGHUP handlers that re-raise
        # ``KeyboardInterrupt`` on every signal. Save the original
        # handlers around the run so the unittest runner can finish
        # cleanly even when timeout(1) sends a signal.
        import signal as _signal

        _saved_handlers = {
            sig: _signal.getsignal(sig)
            for sig in (_signal.SIGINT, _signal.SIGTERM, _signal.SIGHUP)
        }

        bed = None
        start_task = None
        try:
            with patch("bed.main.getpool", return_value=_fake_pool()), \
                 patch.object(bed_main_mod, "WebSocketServer", _NoStartServer), \
                 patch(
                     "bed.main.load_or_create_secret",
                     return_value=(fake_secret, fake_instance),
                 ):
                bed = BED(args, router_class)
                # ``BED.start`` runs an infinite ``while self._running:
                # await asyncio.sleep(1)`` after registering
                # services, so the call only returns when ``stop()``
                # flips the flag. Schedule ``stop`` on the loop a
                # tick later so the registration + router
                # construction get a chance to run first; then
                # await ``start`` to completion.
                start_task = asyncio.create_task(bed.start())
                await asyncio.sleep(0)
                await bed.stop()
                await asyncio.wait_for(start_task, timeout=2.0)
        finally:
            if start_task is not None and not start_task.done():
                start_task.cancel()
                try:
                    await start_task
                except BaseException:
                    pass
            for sig, handler in _saved_handlers.items():
                try:
                    _signal.signal(sig, handler)
                except (OSError, ValueError):
                    pass

        return bed

    async def test_router_receives_session_registry(self):
        """The router constructor must receive ``session_registry``
        so it can look up the session ``AuthService.bind`` wrote.
        Without this the casino router's ``_check_access`` opens a
        fresh ``CasinoSessionManager`` and never finds anything.
        """
        # Snapshot the last kwargs the router saw.
        await self._run_bed_start()
        kwargs = _CapturingRouter.last_init_kwargs["kwargs"]
        self.assertIn(
            "session_registry",
            kwargs,
            "BED.start must forward session_registry to the MessageRouter",
        )
        from bed.api.session import SessionRegistry

        self.assertIsInstance(
            kwargs["session_registry"],
            SessionRegistry,
            "session_registry must be the live bed SessionRegistry, "
            "not a fresh CasinoSessionManager",
        )

    async def test_router_receives_token_wiring(self):
        """The router constructor must receive ``secret``,
        ``token_store``, and ``instance_id`` so the per-op
        defense-in-depth check can re-verify the bearer token on
        every call. Without these the wire-token / session-token
        gates degrade to no-ops and ``claims_were_set`` stays
        False, producing the ``not_authenticated`` envelope.
        """
        await self._run_bed_start()
        kwargs = _CapturingRouter.last_init_kwargs["kwargs"]
        for key in ("secret", "token_store", "instance_id"):
            self.assertIn(
                key,
                kwargs,
                f"BED.start must forward {key} to the MessageRouter",
            )
        self.assertEqual(
            kwargs["instance_id"],
            "router-wiring-test",
            "instance_id must match the one AuthService was built with",
        )

    async def test_router_receives_no_token_kwargs_when_auth_disabled(self):
        """When auth is disabled (``token_persistence="none"`` +
        default-router style) BED has no ``auth_service`` /
        ``token_store`` to forward. The router constructor must
        still be called, but with no token kwargs so its legacy /
        door-mode fallback path stays intact.
        """
        await self._run_bed_start(token_persistence="none")
        kwargs = _CapturingRouter.last_init_kwargs["kwargs"]
        self.assertNotIn(
            "secret",
            kwargs,
            "auth-disabled run must not pass a secret",
        )
        self.assertNotIn(
            "token_store",
            kwargs,
            "auth-disabled run must not pass a token_store",
        )
        self.assertNotIn(
            "instance_id",
            kwargs,
            "auth-disabled run must not pass an instance_id",
        )


if __name__ == "__main__":
    unittest.main()
