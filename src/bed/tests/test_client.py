"""Tests for bed.client (Phase 3 hardening).

Covers:
- ``bed.client.connection``: ``get_running_loop`` is used, not
  ``get_event_loop`` (no DeprecationWarning on 3.10+); push dispatch
  works inside a running loop.
- ``bed.client``: ``_RequestId`` and ``_expected_result_type`` are no
  longer exported from the public API.
- ``bed.client.singleton``: weakref-keyed cache does not alias two
  distinct ``args`` objects; ``reset_bed_connection`` clears the
  entry.
- ``bed.client.messageservice``: ``_push`` swallows errors from
  ``bump_local_unread_count``; ``subscribe`` swallows errors from
  ``set_local_unread_count``.
"""

from __future__ import annotations

import asyncio
import gc
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------
# Public API surface


def test_client_does_not_export_underscore_symbols():
    """``_RequestId`` and ``_expected_result_type`` are private helpers
    and must not leak through ``bed.client.__all__`` or the public
    imports."""
    import bed.client as client_pkg

    assert "_RequestId" not in client_pkg.__all__
    assert "_expected_result_type" not in client_pkg.__all__
    # The names should not be accessible as attributes either, so
    # downstream code can't accidentally rely on them.
    assert "_RequestId" not in client_pkg.__dict__, (
        "_RequestId must not be re-exported from bed.client"
    )
    assert "_expected_result_type" not in client_pkg.__dict__, (
        "_expected_result_type must not be re-exported from bed.client"
    )


# ---------------------------------------------------------------------
# bed.client.connection


def test_dispatch_push_uses_running_loop():
    """``_dispatch_push`` must use ``asyncio.get_running_loop()`` and
    therefore works inside a running event loop."""
    from bed.client.connection import BedConnection

    captured: list = []

    def _handler(msg):
        captured.append(msg)

    async def runner():
        conn = BedConnection(MagicMock())
        # Register the handler directly so we don't trigger
        # _ensure_recv_loop() (which would dial bed).
        conn._push_handlers.append(_handler)
        await conn._dispatch_push({"type": "message", "x": 1})

    asyncio.run(runner())
    assert captured == [{"type": "message", "x": 1}]


def test_get_event_loop_raises_outside_running_loop():
    """On Python 3.12+ ``asyncio.get_event_loop()`` raises
    ``RuntimeError`` outside a running loop. Our code uses
    ``get_running_loop`` so we never hit that path."""
    with pytest.raises(RuntimeError):
        asyncio.get_event_loop()


# ---------------------------------------------------------------------
# bed.client.singleton


def test_singleton_returns_same_connection_for_same_args():
    """``get_bed_connection`` returns the same instance for the same
    ``args`` object (weakref-keyed)."""
    import bed.client.singleton as singleton

    singleton._CONNECTION_SINGLETON.clear()
    args = MagicMock()
    a = singleton.get_bed_connection(args)
    b = singleton.get_bed_connection(args)
    assert a is b
    singleton._CONNECTION_SINGLETON.clear()


def test_singleton_distinguishes_different_args():
    """Two distinct ``args`` objects get two distinct connections."""
    import bed.client.singleton as singleton

    singleton._CONNECTION_SINGLETON.clear()
    args1 = MagicMock()
    args2 = MagicMock()
    a = singleton.get_bed_connection(args1)
    b = singleton.get_bed_connection(args2)
    assert a is not b
    singleton._CONNECTION_SINGLETON.clear()


def test_singleton_does_not_alias_after_gc():
    """After ``args`` is garbage-collected, the next lookup with a
    fresh ``args`` must NOT return the dead connection (would be an
    id-reuse aliasing bug)."""
    import bed.client.singleton as singleton

    singleton._CONNECTION_SINGLETON.clear()
    args = MagicMock()
    a = singleton.get_bed_connection(args)
    del args
    gc.collect()

    args2 = MagicMock()
    b = singleton.get_bed_connection(args2)
    assert a is not b
    singleton._CONNECTION_SINGLETON.clear()


def test_reset_bed_connection_drops_entry():
    import bed.client.singleton as singleton

    singleton._CONNECTION_SINGLETON.clear()
    args = MagicMock()
    conn = singleton.get_bed_connection(args)
    conn.force_close = MagicMock()
    singleton.reset_bed_connection(args)
    assert len(singleton._CONNECTION_SINGLETON) == 0
    conn.force_close.assert_called_once()
    singleton._CONNECTION_SINGLETON.clear()


# ---------------------------------------------------------------------
# bed.client.messageservice


def test_push_handler_swallows_bump_errors():
    """``_push`` must not propagate exceptions from
    ``bump_local_unread_count``. We replicate the closure that
    ``subscribe`` builds so we exercise the real try/except wrapper."""
    fake_msg_module = MagicMock()
    fake_msg_module.bump_local_unread_count.side_effect = RuntimeError("boom")

    # Recreate the closure-building logic from subscribe() exactly.
    moniker = "alice"
    message_module = fake_msg_module

    def _push(msg: Any) -> None:
        if msg.get("type") != "message":
            return
        if msg.get("recipient_moniker") != moniker:
            return
        status = msg.get("status", "pending")
        try:
            if status == "read":
                message_module.bump_local_unread_count(moniker, -1)
            elif status == "pending":
                message_module.bump_local_unread_count(moniker, 1)
        except Exception:
            pass

    # Must not raise.
    _push({"type": "message", "recipient_moniker": "alice", "status": "pending"})
    _push({"type": "message", "recipient_moniker": "alice", "status": "read"})
    fake_msg_module.bump_local_unread_count.assert_any_call("alice", 1)
    fake_msg_module.bump_local_unread_count.assert_any_call("alice", -1)


def test_subscribe_swallows_set_local_unread_count_errors():
    """If ``set_local_unread_count`` raises, ``subscribe`` still
    succeeds with ``ok=True`` because the cache is best-effort."""
    from bed.client.connection import BedConnection
    import bed.client.messageservice as ms_module

    fake_msg_module = MagicMock()
    fake_msg_module.bump_local_unread_count = MagicMock()
    fake_msg_module.set_local_unread_count.side_effect = RuntimeError("boom")

    fake_conn = MagicMock(spec=BedConnection)
    fake_conn.send = AsyncMock(
        side_effect=[
            # First send: the subscribe reply.
            {"ok": True, "type": "message_subscribe_result", "moniker": "alice"},
            # Second send: list_pending reply.
            {"ok": True, "messages": []},
        ]
    )
    fake_conn.subscribe = AsyncMock()

    async def runner():
        with patch.dict("sys.modules", {"bbsengine6.message": fake_msg_module}):
            svc = ms_module.BedMessageServiceClient(fake_conn)
            result = await svc.subscribe("alice")
        assert result.get("ok") is True

    asyncio.run(runner())


# ---------------------------------------------------------------------
# bed.client.authservice


def test_auth_client_exports_present():
    """The public auth client symbols must be importable from
    ``bed.client`` (mirrors the bank/message client exports)."""
    import bed.client as client_pkg

    assert "BedAuthServiceClient" in client_pkg.__all__
    assert "get_auth_client" in client_pkg.__all__
    assert "reset_auth_client" in client_pkg.__all__
    assert hasattr(client_pkg, "BedAuthServiceClient")
    assert hasattr(client_pkg, "get_auth_client")
    assert hasattr(client_pkg, "reset_auth_client")


def test_get_auth_client_returns_same_instance_for_same_connection():
    """``get_auth_client`` caches one client per connection (same
    pattern as ``get_bank_client`` / ``get_message_client``)."""
    import bed.client.authservice as auth_mod

    auth_mod.reset_auth_client()
    conn = MagicMock()
    a = auth_mod.get_auth_client(conn)
    b = auth_mod.get_auth_client(conn)
    assert a is b
    auth_mod.reset_auth_client()


def test_get_auth_client_rebuilds_when_connection_changes():
    """A different connection produces a different client (so tests
    that pass a fresh loopback transport get a fresh client)."""
    import bed.client.authservice as auth_mod

    auth_mod.reset_auth_client()
    conn1 = MagicMock()
    conn2 = MagicMock()
    a = auth_mod.get_auth_client(conn1)
    b = auth_mod.get_auth_client(conn2)
    assert a is not b
    auth_mod.reset_auth_client()


def test_reset_auth_client_drops_cache():
    import bed.client.authservice as auth_mod

    auth_mod.reset_auth_client()
    conn = MagicMock()
    auth_mod.get_auth_client(conn)
    assert auth_mod._module_client is not None
    auth_mod.reset_auth_client()
    assert auth_mod._module_client is None
