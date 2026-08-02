"""Tests for bed.api.message.MessageService.

Covers subscription state, dispatch logic, list_pending, and lifecycle
without requiring a live PostgreSQL connection. The LISTEN loop is
exercised via a mocked async connection.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_args():
    args = MagicMock()
    args.databasename = "test_db"
    args.databasehost = "localhost"
    args.databaseport = 5432
    args.databaseuser = "test_user"
    args.databasepassword = "test_pass"
    return args


def _make_server():
    server = MagicMock()
    server.send_to = AsyncMock()
    server.register_service = MagicMock()
    return server


def _make_session_manager():
    return MagicMock()


def test_message_service_registers_handled_types():
    """MessageService.register_all registers the three message types."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    server = _make_server()
    service.register_all(server)

    assert server.register_service.call_count == 1
    call_args = server.register_service.call_args
    assert call_args[0][0] is service
    types = call_args[0][1]
    assert "message_subscribe" in types
    assert "message_unsubscribe" in types
    assert "message_list_pending" in types


def test_subscribe_adds_to_subscribed_map():
    """_handle_subscribe records the websocket under the moniker."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    ws = MagicMock()
    msg = {"type": "message_subscribe", "moniker": "alice"}

    result = asyncio.run(service._handle_subscribe(ws, msg))

    assert result["type"] == "message_subscribe_result"
    assert result["ok"] is True
    assert result["moniker"] == "alice"
    assert service._subscribed["alice"] is ws


def test_subscribe_rejects_empty_moniker():
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    msg = {"type": "message_subscribe", "moniker": "  "}

    result = asyncio.run(service._handle_subscribe(MagicMock(), msg))

    assert result["ok"] is False
    assert result["code"] == "missing_moniker"


def test_unsubscribe_removes_from_map():
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    ws = MagicMock()
    asyncio.run(
        service._handle_subscribe(ws, {"type": "message_subscribe", "moniker": "bob"})
    )
    assert "bob" in service._subscribed

    result = asyncio.run(
        service._handle_unsubscribe(
            ws, {"type": "message_unsubscribe", "moniker": "bob"}
        )
    )

    assert result["ok"] is True
    assert "bob" not in service._subscribed


def test_dispatch_notification_sends_to_subscribed_websocket():
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    server = _make_server()
    service.server = server
    ws = MagicMock()
    asyncio.run(
        service._handle_subscribe(ws, {"type": "message_subscribe", "moniker": "carol"})
    )

    payload = json.dumps(
        {
            "message_id": 42,
            "recipient_id": 7,
            "recipient_moniker": "carol",
            "status": "pending",
            "urgency": "URGENT",
            "datestamp": "2026-07-22T12:00:00Z",
        }
    )

    asyncio.run(service._dispatch_notification(payload))

    assert server.send_to.await_count == 1
    args = server.send_to.await_args
    assert args[0][0] is ws
    envelope = args[0][1]
    assert envelope["type"] == "message"
    assert envelope["message_id"] == 42
    assert envelope["urgency"] == "URGENT"
    assert envelope["recipient_moniker"] == "carol"


def test_dispatch_notification_no_subscriber_is_noop():
    """If no websocket is subscribed for the moniker, no send happens."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    server = _make_server()
    service.server = server

    payload = json.dumps(
        {"message_id": 1, "recipient_moniker": "ghost", "status": "pending"}
    )
    asyncio.run(service._dispatch_notification(payload))

    assert server.send_to.await_count == 0


def test_dispatch_notification_bad_payload_is_noop():
    """Malformed JSON is silently dropped, doesn't raise."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    server = _make_server()
    service.server = server

    asyncio.run(service._dispatch_notification("not json {{{"))

    assert server.send_to.await_count == 0


def test_dispatch_notification_removes_dead_subscriber():
    """If send_to fails, the dead subscription is removed."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    server = _make_server()
    server.send_to = AsyncMock(side_effect=ConnectionError("ws closed"))
    service.server = server
    ws = MagicMock()
    asyncio.run(
        service._handle_subscribe(ws, {"type": "message_subscribe", "moniker": "dave"})
    )

    payload = json.dumps(
        {"message_id": 1, "recipient_moniker": "dave", "status": "pending"}
    )
    asyncio.run(service._dispatch_notification(payload))

    assert "dave" not in service._subscribed


def test_list_pending_returns_db_messages():
    """_handle_list_pending delegates to bbsengine6.message.get_pending_messages."""
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake_messages = [
        {"id": 1, "content": "hello", "urgency": "ROUTINE"},
        {"id": 2, "content": "world", "urgency": "URGENT"},
    ]

    with patch.object(
        message_module, "get_pending_messages", return_value=fake_messages
    ) as m:
        service = MessageService(_make_args(), _make_session_manager())
        result = asyncio.run(
            service._handle_list_pending(
                {"type": "message_list_pending", "moniker": "alice"}
            )
        )

    assert result["ok"] is True
    assert result["moniker"] == "alice"
    assert result["messages"] == fake_messages
    m.assert_called_once_with("alice", limit=100)


def test_list_pending_rejects_empty_moniker():
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    result = asyncio.run(
        service._handle_list_pending(
            {"type": "message_list_pending", "moniker": ""}
        )
    )

    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    assert result["messages"] == []


def test_lifecycle_start_stop_is_idempotent():
    """start_listener is idempotent; stop_listener cleans up."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())

    mock_conn = MagicMock()
    mock_conn.close = AsyncMock()

    fake_cursor_cm = MagicMock()
    fake_cursor_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    fake_cursor_cm.__aexit__ = AsyncMock(return_value=None)
    mock_conn.cursor = MagicMock(return_value=fake_cursor_cm)
    mock_conn.notifies = AsyncMock(side_effect=asyncio.CancelledError())

    async def runner():
        with patch("psycopg.AsyncConnection.connect", new=AsyncMock(return_value=mock_conn)):
            await service.start_listener()
            await service.start_listener()
            await asyncio.sleep(0.05)
            await service.stop_listener()

    asyncio.run(runner())
    assert service._listener_task is None
    assert service._async_conn is None


# ---------------------------------------------------------------------
# Phase 3 hardening: dependency imports moved to module top, idempotent
# _close_async_conn, narrowed except clause, graceful psycopg absence.


def test_close_async_conn_is_idempotent_when_none():
    """Calling _close_async_conn with no open connection is a no-op."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    asyncio.run(service._close_async_conn())
    assert service._async_conn is None


def test_close_async_conn_clears_ref_after_close():
    """After _close_async_conn, the internal ref is cleared so a second
    call does NOT re-close the same connection."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())

    fake_conn = MagicMock()
    fake_conn.close = AsyncMock()
    service._async_conn = fake_conn

    async def runner():
        await service._close_async_conn()
        await service._close_async_conn()  # second call must be no-op

    asyncio.run(runner())
    fake_conn.close.assert_awaited_once()
    assert service._async_conn is None


def test_listen_loop_handles_psycopg_missing_dependency():
    """When psycopg is None (ImportError at module load), the LISTEN
    loop returns immediately without raising."""
    from bed.api import message as message_module

    service = message_module.MessageService(_make_args(), _make_session_manager())

    async def runner():
        with patch.object(message_module, "psycopg", None):
            await service._listen_loop()

    # Must not raise.
    asyncio.run(runner())
    assert service._async_conn is None


def test_listen_loop_propagates_cancelled_error():
    """asyncio.CancelledError is NOT swallowed by the broad except."""
    from bed.api import message as message_module

    service = message_module.MessageService(_make_args(), _make_session_manager())

    fake_psycopg = MagicMock()
    fake_conn = MagicMock()
    fake_conn.close = AsyncMock()
    fake_psycopg.AsyncConnection.connect = AsyncMock(return_value=fake_conn)
    # Use a dedicated exception class so generic TypeErrors (like
    # ``'MagicMock' object can't be awaited``) don't accidentally
    # match the psycopg.Error branch.
    class _FakePsycopgError(Exception):
        pass
    fake_psycopg.Error = _FakePsycopgError

    fake_cursor_cm = MagicMock()
    fake_cur = MagicMock()
    fake_cur.execute = AsyncMock(return_value=None)
    fake_cursor_cm.__aenter__ = AsyncMock(return_value=fake_cur)
    fake_cursor_cm.__aexit__ = AsyncMock(return_value=None)
    fake_conn.cursor = MagicMock(return_value=fake_cursor_cm)
    fake_conn.notifies = AsyncMock(side_effect=asyncio.CancelledError())

    async def runner():
        with patch.object(message_module, "psycopg", fake_psycopg):
            with pytest.raises(asyncio.CancelledError):
                await service._listen_loop()

    asyncio.run(runner())


def test_listen_loop_recovers_from_psycopg_operational_error():
    """A psycopg.OperationalError on connect causes a graceful
    backoff retry, not a crash."""
    from bed.api import message as message_module

    class _FakePsycopgError(Exception):
        pass

    service = message_module.MessageService(_make_args(), _make_session_manager())

    fake_psycopg = MagicMock()
    fake_psycopg.AsyncConnection.connect = AsyncMock(
        side_effect=_FakePsycopgError("operational: conn refused")
    )
    fake_psycopg.Error = _FakePsycopgError

    async def runner():
        # Stop after one iteration of the outer reconnect loop.
        async def _stop():
            await asyncio.sleep(0.05)
            service._stop_event.set()

        with patch.object(message_module, "psycopg", fake_psycopg):
            await asyncio.gather(service._listen_loop(), _stop())

    # Must not raise.
    asyncio.run(runner())
    assert service._async_conn is None


# ---------------------------------------------------------------------
# Phase 6 hardening: fuzz NOTIFY payload parsing.


class TestDispatchNotificationFuzz:
    """``_dispatch_notification`` must never raise on adversarial
    payloads; everything is logged + dropped."""

    def _make_service(self):
        from bed.api.message import MessageService
        return MessageService(_make_args(), _make_session_manager())

    def test_payloads_that_must_not_raise(self):
        """Each payload is processed by _dispatch_notification.
        Garbage payloads are dropped silently; valid payloads reach the
        subscriber."""
        service = self._make_service()
        server = _make_server()
        service.server = server

        # A live subscriber so valid payloads can be dispatched.
        captured: list = []

        async def _capture_send_to(ws, envelope):
            captured.append(envelope)

        server.send_to.side_effect = _capture_send_to

        ws = MagicMock()

        async def _attach():
            service._subscribed["alice"] = ws

        asyncio.run(_attach())

        payloads = [
            "",                                          # empty
            "null",                                       # json null
            "[]",                                         # json array
            "{}",                                         # empty obj
            '{"recipient_moniker": ""}',                  # empty moniker
            '{"recipient_moniker": 12345}',               # non-string moniker
            '{"recipient_moniker": "alice"}',             # valid
            "not-json-at-all",                            # raw garbage
            "\x00\x01\x02",                               # bytes
            "null" * 500,                                 # long junk
            '{"recipient_moniker": "alice", "extra": ' + "x" * 5000 + '}',
        ]

        async def runner():
            for p in payloads:
                await service._dispatch_notification(p)

        asyncio.run(runner())
        # Only the well-formed payload with a real moniker made it to
        # the subscriber.
        assert len(captured) == 1
        assert captured[0]["recipient_moniker"] == "alice"


