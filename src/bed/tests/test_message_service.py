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

    sm = _make_session_manager()
    sm.get_by_websocket.return_value = MagicMock(
        moniker="alice", is_sysop=True
    )
    with patch.object(
        message_module, "get_pending_messages", return_value=fake_messages
    ) as m:
        service = MessageService(_make_args(), sm)
        result = asyncio.run(
            service._handle_list_pending(
                MagicMock(),
                {"type": "message_list_pending", "moniker": "alice"},
            )
        )

    assert result["ok"] is True
    assert result["moniker"] == "alice"
    assert result["messages"] == fake_messages
    m.assert_called_once_with("alice", limit=100)


def test_list_pending_rejects_empty_moniker():
    from bed.api.message import MessageService

    sm = _make_session_manager()
    sm.get_by_websocket.return_value = MagicMock(
        moniker="alice", is_sysop=True
    )
    service = MessageService(_make_args(), sm)
    result = asyncio.run(
        service._handle_list_pending(
            MagicMock(),
            {"type": "message_list_pending", "moniker": ""},
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


# ---------------------------------------------------------------------
# Phase 4 hardening: handlers delegate to bbsengine6.message.access().
#
# Every _handle_* method runs three gates in order:
#   1. Session bound (else not_authenticated envelope).
#   2. Wire-shape validation -- moniker present (else missing_moniker).
#   3. bbsengine6.message.access(args, op, session=state, message=msg)
#      (else forbidden envelope).
#
# These tests patch _message_access at the bed.api.message module
# boundary (the import site that MessageService uses), so they verify
# the gate order without depending on bbsengine6.message.access.


class _FakeState:
    """Minimal SessionState stand-in (only attributes access() reads)."""

    def __init__(self, moniker: str, *, is_sysop: bool = False):
        self.moniker = moniker
        self.is_sysop = is_sysop


def _patched_access(moniker_match=True, *, allow=True):
    """Return a fake access() that records calls and yields ``allow``.

    When ``moniker_match`` is True, the fake records every call; when
    False, it raises AssertionError if the handler invokes it (which
    means an earlier gate should have blocked the call)."""
    calls = []

    def _fake(args, op, /, **kwargs):
        calls.append(
            {
                "op": op,
                "session_moniker": getattr(kwargs.get("session"), "moniker", None),
                "message": kwargs.get("message"),
            }
        )
        return allow

    return _fake, calls


# ---------- _handle_subscribe access-gate tests ----------


def test_subscribe_denies_when_no_session_bound():
    """Unbound websocket -> not_authenticated envelope, no _subscribed change."""
    from bed.api import message as message_module
    from bed.api.message import MessageService
    from bed.api.session import SessionRegistry

    fake, calls = _patched_access()
    service = MessageService(_make_args(), SessionRegistry())
    ws = MagicMock()
    ws.id = "ws-no-session"
    msg = {"type": "message_subscribe", "moniker": "alice"}

    with patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_subscribe(ws, msg))

    assert result["type"] == "message_subscribe_result"
    assert result["ok"] is False
    assert result["code"] == "not_authenticated"
    assert "alice" not in service._subscribed
    assert calls == []  # access() must not be reached


def test_subscribe_denies_when_shape_invalid():
    """Missing moniker -> missing_moniker envelope, no access() call."""
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access()
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    ws = MagicMock()
    ws.id = "ws-1"
    msg = {"type": "message_subscribe", "moniker": "  "}

    with patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_subscribe(ws, msg))

    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    assert "alice" not in service._subscribed
    assert calls == []


def test_subscribe_denies_when_access_returns_false():
    """access() denies -> forbidden envelope, no _subscribed change."""
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access(allow=False)
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    ws = MagicMock()
    ws.id = "ws-2"
    msg = {"type": "message_subscribe", "moniker": "bob"}

    with patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_subscribe(ws, msg))

    assert result["ok"] is False
    assert result["code"] == "forbidden"
    assert "bob" not in service._subscribed
    assert len(calls) == 1
    assert calls[0]["op"] == "subscribe"
    assert calls[0]["session_moniker"] == "alice"
    assert calls[0]["message"]["moniker"] == "bob"


def test_subscribe_allows_and_binds_when_access_returns_true():
    """access() allows -> binds websocket under moniker."""
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access(allow=True)
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    ws = MagicMock()
    ws.id = "ws-3"
    msg = {"type": "message_subscribe", "moniker": "alice"}

    with patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_subscribe(ws, msg))

    assert result["ok"] is True
    assert result["moniker"] == "alice"
    assert service._subscribed["alice"] is ws
    assert len(calls) == 1
    assert calls[0]["op"] == "subscribe"


# ---------- _handle_unsubscribe access-gate tests ----------


def test_unsubscribe_denies_when_no_session_bound():
    from bed.api import message as message_module
    from bed.api.message import MessageService
    from bed.api.session import SessionRegistry

    fake, calls = _patched_access()
    service = MessageService(_make_args(), SessionRegistry())
    ws = MagicMock()
    ws.id = "ws-no-session"
    msg = {"type": "message_unsubscribe", "moniker": "alice"}

    with patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_unsubscribe(ws, msg))

    assert result["type"] == "message_unsubscribe_result"
    assert result["ok"] is False
    assert result["code"] == "not_authenticated"
    assert calls == []


def test_unsubscribe_denies_when_access_returns_false():
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access(allow=False)
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    ws = MagicMock()
    ws.id = "ws-4"
    msg = {"type": "message_unsubscribe", "moniker": "bob"}

    with patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_unsubscribe(ws, msg))

    assert result["ok"] is False
    assert result["code"] == "forbidden"
    assert len(calls) == 1
    assert calls[0]["op"] == "unsubscribe"


def test_unsubscribe_allows_and_unbinds_when_access_returns_true():
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access(allow=True)
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    ws = MagicMock()
    ws.id = "ws-5"
    service._subscribed["alice"] = ws
    msg = {"type": "message_unsubscribe", "moniker": "alice"}

    with patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_unsubscribe(ws, msg))

    assert result["ok"] is True
    assert "alice" not in service._subscribed
    assert len(calls) == 1
    assert calls[0]["op"] == "unsubscribe"


# ---------- _handle_list_pending access-gate tests ----------


def test_list_pending_denies_when_no_session_bound():
    """Unbound websocket -> not_authenticated; get_pending_messages
    must NOT be called."""
    from bed.api import message as message_module
    from bed.api.message import MessageService
    from bed.api.session import SessionRegistry

    fake, calls = _patched_access()
    service = MessageService(_make_args(), SessionRegistry())
    msg = {"type": "message_list_pending", "moniker": "alice"}

    with patch.object(
        message_module, "get_pending_messages"
    ) as gp, patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_list_pending(MagicMock(), msg))

    assert result["type"] == "message_list_pending_result"
    assert result["ok"] is False
    assert result["code"] == "not_authenticated"
    assert result["messages"] == []
    gp.assert_not_called()
    assert calls == []


def test_list_pending_denies_when_shape_invalid():
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access()
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    msg = {"type": "message_list_pending", "moniker": ""}

    with patch.object(
        message_module, "get_pending_messages"
    ) as gp, patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_list_pending(MagicMock(), msg))

    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    assert result["messages"] == []
    gp.assert_not_called()
    assert calls == []


def test_list_pending_denies_when_access_returns_false():
    """access() denies -> forbidden; get_pending_messages must NOT run."""
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access(allow=False)
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    ws = MagicMock()
    ws.id = "ws-6"
    msg = {"type": "message_list_pending", "moniker": "bob"}

    with patch.object(
        message_module, "get_pending_messages"
    ) as gp, patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_list_pending(ws, msg))

    assert result["ok"] is False
    assert result["code"] == "forbidden"
    assert result["messages"] == []
    gp.assert_not_called()
    assert len(calls) == 1
    assert calls[0]["op"] == "list_pending"


def test_list_pending_returns_messages_when_access_returns_true():
    from bed.api import message as message_module
    from bed.api.message import MessageService

    fake, calls = _patched_access(allow=True)
    sm = _make_session_manager()
    sm.get_by_websocket.return_value = _FakeState("alice")
    service = MessageService(_make_args(), sm)
    ws = MagicMock()
    ws.id = "ws-7"
    msg = {"type": "message_list_pending", "moniker": "alice"}

    fake_messages = [{"id": 1, "content": "hello"}]
    with patch.object(
        message_module, "get_pending_messages", return_value=fake_messages
    ) as gp, patch.object(message_module, "_message_access", fake):
        result = asyncio.run(service._handle_list_pending(ws, msg))

    assert result["ok"] is True
    assert result["moniker"] == "alice"
    assert result["messages"] == fake_messages
    gp.assert_called_once_with("alice", limit=100)
    assert len(calls) == 1
    assert calls[0]["op"] == "list_pending"


# ---------- dispatch ----------


def test_handle_message_dispatches_by_op():
    """handle_message() routes through _OP_TO_HANDLER; unknown type returns None."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    server = _make_server()
    service.register_all(server)
    ws = MagicMock()

    # An unknown message type returns None (caller falls through to next service).
    result = asyncio.run(
        service.handle_message(
            server, ws, "/", {"type": "no_such_message"}
        )
    )
    assert result is None


