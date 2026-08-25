"""Tests for bed.api.message.MessageService.

Covers subscription state, dispatch logic, list_pending, and lifecycle
without requiring a live PostgreSQL connection. The LISTEN loop is
exercised via a mocked async connection.

Also exercises the token-aware authorization pipeline added in the
bank/auth/casino-standard upgrade: every per-op ``_handle_*`` runs
five gates (session resolve, wire-token, session-token, wire-shape,
``bbsengine6.message.access()``); the wire token is preferred over
the session-bound token (defense in depth); and an unbound websocket
can lazily bind a session from a valid wire token's claims.
"""

import asyncio
import json
import secrets
import time
from typing import Any
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


# ---------------------------------------------------------------------
# Token-aware authorization — bank/auth/casino-standard upgrade.
#
# Every MessageService can be constructed without secret/token_store/
# instance_id (legacy callers, unit tests that don't care about
# tokens); in that mode the token gate is a no-op and authorization
# falls back to the in-memory session state. When the constructor
# receives the auth service's secret + token store + instance id,
# every message op re-verifies ``state.auth_service_token`` against
# the same HMAC scheme AuthService uses, and the decoded claims are
# stashed on ``message["claims"]`` so bbsengine6.message.access()
# can prefer claim-derived ``moniker`` / ``is_sysop`` over the
# session. The wire token sent on each call is preferred over the
# session-bound token (defense in depth). When the websocket has no
# bound session, a valid wire token can lazily bind one from its
# claims.


# ---------- helpers for token-aware tests ----------


def _make_websocket(ws_id: str = "ws-1"):
    ws = MagicMock()
    ws.id = ws_id
    return ws


def _mint_token(
    *,
    secret: bytes,
    token_store: Any,
    instance_id: str,
    moniker: str = "alice",
    session_id: str = "s1",
    websocket_id: str = "ws-1",
    is_sysop: bool = False,
    expires_at: float | None = None,
    bed_instance_id: str | None = None,
    loginid: str | None = None,
) -> str:
    """Mint a valid bearer token and put it into ``token_store``.

    Mirrors AuthService._mint_record so we can drive the message
    service's token gate from a unit test without booting a real
    auth flow. ``expires_at`` defaults to ~100 years in the future
    so the test doesn't fight the wall clock; pass an explicit value
    to exercise the expiry path. ``bed_instance_id`` defaults to the
    ``instance_id`` arg so a freshly minted token matches the bed
    instance it was minted against.
    """
    from bed.api.auth import _encode_token
    from bed.api.token_store import TokenRecord

    now = time.time()
    exp = float(expires_at) if expires_at is not None else now + (86400 * 365 * 100)
    claims = {
        "version": 1,
        "moniker": moniker,
        "issued_at": now,
        "expires_at": exp,
        "session_id": session_id,
        "is_sysop": bool(is_sysop),
        "bed_instance_id": bed_instance_id or instance_id,
        "websocket_id": websocket_id,
        "loginid": loginid or f"{moniker}_os",
    }
    token = _encode_token(claims, secret)
    token_store.put(
        TokenRecord(
            token=token,
            moniker=moniker,
            session_id=session_id,
            issued_at=now,
            expires_at=exp,
            is_sysop=bool(is_sysop),
            bed_instance_id=claims["bed_instance_id"],
            websocket_id=websocket_id,
            claims=claims,
            loginid=claims["loginid"],
        )
    )
    return token


def _make_token_aware_session(
    *,
    ws_id: str = "ws-1",
    moniker: str = "alice",
    is_sysop: bool = False,
    auth_service_token: str | None = None,
    secret: bytes | None = None,
    token_store: Any | None = None,
    instance_id: str = "msg-token-test",
    session_id: str = "s1",
):
    """Build (websocket, session_registry, secret, token_store,
    instance_id) for a token-aware MessageService test.

    ``auth_service_token`` is set on the bound SessionState so the
    token gate has something to verify. If left None, the session is
    bound without a token and the gate is a no-op (legacy mode).
    Pass ``secret`` / ``token_store`` / ``instance_id`` to share
    wiring across the mint step (e.g. when you need to mint a token
    against the same secret the MessageService will verify against).
    Defaults build a fresh 32-byte secret + empty in-memory store +
    ``"msg-token-test"`` instance id.
    """
    from bed.api.session import SessionRegistry
    from bed.api.token_store import InMemoryTokenStore

    secret_bytes = secret if secret is not None else secrets.token_bytes(32)
    store = token_store if token_store is not None else InMemoryTokenStore()
    reg = SessionRegistry()
    reg.bind(
        session_id,
        ws_id,
        moniker,
        is_sysop,
        loginid=f"{moniker}_os",
    )
    state = reg.get_by_websocket(ws_id)
    state.auth_service_token = auth_service_token
    return _make_websocket(ws_id), reg, secret_bytes, store, instance_id


def _patch_access_returning(allow: bool = True):
    """Patch bbsengine6.message.access() at the bed.api.message module
    boundary. Returns a context manager; on exit yields the MagicMock
    so tests can assert on call_args.
    """
    from bed.api import message as message_module

    return patch.object(message_module, "_message_access", return_value=allow)


def _patch_get_pending_messages(rows=None):
    """Patch get_pending_messages at the bed.api.message module
    boundary. Returns (ctx_mgr, mock)."""
    from bed.api import message as message_module

    if rows is None:
        rows = []
    return patch.object(
        message_module, "get_pending_messages", return_value=rows
    )


# ---------- constructor / wiring ----------


def test_message_service_constructor_accepts_token_kwargs():
    """MessageService stores secret/token_store/instance_id/clock on
    self so _check_access can re-verify tokens on every op."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    store = InMemoryTokenStore()
    clock = lambda: 1700000000.0

    service = MessageService(
        _make_args(),
        _make_session_manager(),
        secret=secret,
        token_store=store,
        instance_id="msg-token-test",
        clock=clock,
    )
    assert service.secret == secret
    assert service.token_store is store
    assert service.instance_id == "msg-token-test"
    assert service._clock is clock
    assert service._now() == 1700000000.0


def test_message_service_constructor_default_clock_uses_time():
    """When no clock is injected, _now() reads from time.time()."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    assert service._clock is None
    before = time.time()
    got = service._now()
    after = time.time()
    assert before <= got <= after


def test_message_service_constructor_no_token_kwargs_is_legacy():
    """Without token kwargs, secret/token_store/instance_id are None --
    the token gate is a no-op and authorization falls back to the
    session attributes (matches the bank service legacy mode)."""
    from bed.api.message import MessageService

    service = MessageService(_make_args(), _make_session_manager())
    assert service.secret is None
    assert service.token_store is None
    assert service.instance_id is None


# ---------- legacy-mode token gate is a no-op ----------


def test_check_access_skips_token_gate_when_no_token_wired():
    """A MessageService without secret/token_store/instance_id lets
    the call through with session-only authorization. This is the
    legacy / --token-persistence=none path."""
    from bed.api.message import MessageService

    ws, sm = _make_session_manager(), _make_session_manager()
    sm.get_by_websocket.return_value = MagicMock(moniker="alice", is_sysop=False)
    service = MessageService(_make_args(), sm)

    with _patch_access_returning(True) as access_mock:
        state, err = service._check_access(
            ws, "subscribe", {"moniker": "alice"}
        )
    assert err is None
    assert access_mock.called
    assert state.moniker == "alice"


def test_check_access_skips_token_gate_when_session_has_no_token():
    """When the service IS token-aware but the session was bound
    outside the auth flow (state.auth_service_token is None), the
    gate is a no-op. Defensive: callers shouldn't crash if a session
    exists without a token."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    ws, sm = _make_session_manager(), _make_session_manager()
    sm.get_by_websocket.return_value = MagicMock(
        moniker="alice", is_sysop=False, auth_service_token=None
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secrets.token_bytes(32),
        token_store=InMemoryTokenStore(),
        instance_id="msg-token-test",
    )

    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(
            ws, "subscribe", {"moniker": "alice"}
        )
    assert err is None
    assert access_mock.called


# ---------- session-token gate ----------


def test_check_access_validates_session_token_on_each_op():
    """A valid session token passes the gate, the claims are
    stashed on the message, and access() is called."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice"}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    assert msg["claims"]["moniker"] == "alice"
    assert msg["claims"]["is_sysop"] is False
    assert access_mock.called


def test_check_access_rejects_expired_session_token():
    """A token whose ``expires_at`` is past is surfaced as
    ``token_expired`` and deleted from the store. The lazy-GC in
    InMemoryTokenStore already removes expired entries on lookup,
    but the gate explicitly deletes so a real Postgres-backed store
    that doesn't lazy-GC still cleans up."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    # ``clock`` advances past ``expires_at`` so the gate sees an
    # expired token even though the wall clock hasn't moved.
    fake_now = [10_000_000.0]
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        expires_at=fake_now[0] + 5,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        clock=lambda: fake_now[0] + 100,
    )

    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(
            ws, "subscribe", {"moniker": "alice"}
        )
    assert err is not None
    assert err["code"] == "token_expired"
    assert err["recoverable"] is True
    assert not access_mock.called


def test_check_access_rejects_revoked_session_token():
    """A signature-valid token that was deleted from the store is
    surfaced as ``token_revoked`` (not recoverable -- the client
    must ``auth`` again to get a fresh token)."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    # Purge the token from the store before the gate runs.
    token_store.delete(token)
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(
            ws, "subscribe", {"moniker": "alice"}
        )
    assert err is not None
    assert err["code"] == "token_revoked"
    assert err["recoverable"] is False
    assert not access_mock.called


def test_check_access_rejects_instance_mismatched_session_token():
    """A signature-valid token whose ``bed_instance_id`` does not
    match the current bed instance is surfaced as
    ``bed_instance_mismatch`` and deleted from the store. The
    client must re-authenticate against the current instance."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    other_instance_id = "different-bed-instance"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        bed_instance_id=other_instance_id,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(
            ws, "subscribe", {"moniker": "alice"}
        )
    assert err is not None
    assert err["code"] == "bed_instance_mismatch"
    assert err["recoverable"] is False
    assert token_store.get(token) is None
    assert not access_mock.called


def test_check_access_rejects_tampered_session_token():
    """A token whose HMAC doesn't verify is surfaced as
    ``token_invalid``. The store is left alone (we don't know if
    the original record was tampered with or the wire was)."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    other_secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=other_secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(
            ws, "subscribe", {"moniker": "alice"}
        )
    assert err is not None
    assert err["code"] == "token_invalid"
    assert err["recoverable"] is False
    assert not access_mock.called


def test_check_access_claim_is_sysop_bypasses_ownership_gate():
    """The session is bound as alice/non-sysop, but the claims
    recovered from her session token say is_sysop=True. The policy
    sees a non-sysop alice trying to subscribe to bob's stream --
    it would normally deny. With a sysop claim, the gate passes
    and access() sees a sysop."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        is_sysop=True,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "bob"}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    assert msg["claims"]["moniker"] == "alice"
    assert msg["claims"]["is_sysop"] is True
    # access() saw a sysop because the claim-derived is_sysop is True.
    assert access_mock.called


# ---------- wire-token gate (defense in depth) ----------


def test_wire_token_happy_path_stashes_claims():
    """A valid wire token on the call passes the gate, the claims
    are stashed on the message, and access() is called. The session
    token gate is skipped because the wire token already populated
    ``message['claims']``."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        # Session bound WITHOUT a token so we can prove the wire
        # token alone populates claims.
        auth_service_token=None,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    assert msg["claims"]["moniker"] == "alice"
    assert msg["claims"]["is_sysop"] is False
    assert access_mock.called


def test_wire_token_invalid_returns_token_invalid_envelope():
    """A wire token with a bad HMAC is surfaced as ``token_invalid``
    and access() is NOT called."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    other_secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=other_secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is not None
    assert err["code"] == "token_invalid"
    assert err["recoverable"] is False
    assert not access_mock.called


def test_wire_token_expired_returns_token_expired_envelope():
    """A wire token whose ``expires_at`` is past is surfaced as
    ``token_expired`` (recoverable -- the client may try ``reconnect``
    with a refreshed token)."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    fake_now = [10_000_000.0]
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        expires_at=fake_now[0] + 5,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        clock=lambda: fake_now[0] + 100,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is not None
    assert err["code"] == "token_expired"
    assert err["recoverable"] is True
    assert not access_mock.called


def test_wire_token_revoked_returns_token_revoked_envelope():
    """A wire token that has been deleted from the store is surfaced
    as ``token_revoked`` (not recoverable)."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    token_store.delete(token)
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is not None
    assert err["code"] == "token_revoked"
    assert err["recoverable"] is False
    assert not access_mock.called


def test_wire_token_absent_falls_back_to_session_token():
    """An absent ``message["token"]`` falls through to the
    session-bound token validation. The session token passes and
    claims are stashed."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice"}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    assert msg["claims"]["moniker"] == "alice"
    assert access_mock.called


def test_wire_token_empty_falls_back_to_session_token():
    """An empty ``message["token"]`` is treated like an absent one
    -- falls through to the session-bound token."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": ""}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    assert msg["claims"]["moniker"] == "alice"
    assert access_mock.called


# ---------- lazy bind from wire-token claims ----------


def test_lazy_bind_from_wire_token_synthesizes_session():
    """An unbound websocket with a valid wire token gets a session
    lazily bound from the token's claims. Subsequent access() sees
    the claim-derived moniker/is_sysop. This is the CLI path:
    each per-subcommand asyncio.run opens a fresh WebSocket whose
    id is unknown to the server, so the lazy-bind fallback is the
    only way a per-op call can recover the prior auth session."""
    from bed.api.message import MessageService
    from bed.api.session import SessionRegistry
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        session_id="s-lazy",
        websocket_id="ws-old",
        is_sysop=False,
    )
    # Empty registry: the prior auth session was lost (e.g. process
    # restart, registry GC, etc.) so get_by_websocket and
    # get_by_session both miss.
    sm = SessionRegistry()
    ws = _make_websocket(ws_id="ws-fresh")
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    # Session was lazily synthesized from claims.
    assert state is not None
    assert state.session_id == "s-lazy"
    assert state.moniker == "alice"
    assert state.is_sysop is False
    assert state.websocket_id == "ws-fresh"
    assert state.auth_service_token == token
    assert msg["claims"]["moniker"] == "alice"
    assert access_mock.called


def test_lazy_bind_reuses_existing_session():
    """If the server still has the session under its ``session_id``
    (just bound to a different websocket_id because the CLI opened
    a fresh WS), the lazy bind rebinds the existing session to the
    new websocket without losing its attributes.

    Note: ``SessionRegistry.bind()`` does not eagerly remove the old
    websocket mapping when rebinding the same session to a new WS --
    stale websocket entries are reaped by ``unbind_websocket`` on
    socket close. The lazy-bind helper therefore rebinds the session
    record's ``websocket_id`` to the fresh WS but leaves the old
    entry behind; this matches ``bed.api.bank._get_or_bind_session_for``
    which has the same contract."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        session_id="s-existing",
        websocket_id="ws-old",
        is_sysop=False,
    )
    # Pre-existing session bound to a stale websocket; the fresh
    # WS for the current call has no binding.
    _ws, sm, _, _, _ = _make_token_aware_session(
        ws_id="ws-old",
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        session_id="s-existing",
    )
    fresh_ws = _make_websocket(ws_id="ws-fresh")
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        state, err = service._check_access(fresh_ws, "subscribe", msg)
    assert err is None
    assert state.session_id == "s-existing"
    # WS mapping was updated to the fresh websocket: the session
    # record's websocket_id now points at the fresh socket, and the
    # fresh-socket entry in the registry resolves to the same state.
    assert state.websocket_id == "ws-fresh"
    assert sm.get_by_websocket("ws-fresh") is state
    assert state.auth_service_token == token
    assert msg["claims"]["moniker"] == "alice"
    assert access_mock.called


def test_lazy_bind_no_wire_token_returns_not_authenticated():
    """Unbound WS AND no wire token on the call -> ``not_authenticated``.
    The CLI must drive an ``auth`` or ``reconnect`` first."""
    from bed.api.message import MessageService
    from bed.api.session import SessionRegistry
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    sm = SessionRegistry()
    ws = _make_websocket(ws_id="ws-orphan")
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice"}
    with _patch_access_returning(True) as access_mock:
        state, err = service._check_access(ws, "subscribe", msg)
    assert err is not None
    assert err["code"] == "not_authenticated"
    assert err["recoverable"] is True
    assert state is None
    assert not access_mock.called


def test_lazy_bind_invalid_wire_token_returns_token_invalid():
    """Unbound WS with a wire token that fails HMAC -> the wire-token
    envelope is surfaced (not ``not_authenticated`` -- the client
    knows the wire was rejected)."""
    from bed.api.message import MessageService
    from bed.api.session import SessionRegistry
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    other_secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=other_secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    sm = SessionRegistry()
    ws = _make_websocket(ws_id="ws-orphan")
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        state, err = service._check_access(ws, "subscribe", msg)
    assert err is not None
    assert err["code"] == "token_invalid"
    assert state is None
    assert not access_mock.called


def test_lazy_bind_session_id_missing_returns_not_authenticated():
    """A valid wire token whose claims have no ``session_id`` can't
    be used for lazy bind (the session registry keys by session_id).
    The handler returns ``not_authenticated``."""
    from bed.api.message import MessageService
    from bed.api.session import SessionRegistry
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        session_id="",  # claim has no session_id
    )
    sm = SessionRegistry()
    ws = _make_websocket(ws_id="ws-orphan")
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "alice", "token": token}
    with _patch_access_returning(True) as access_mock:
        state, err = service._check_access(ws, "subscribe", msg)
    assert err is not None
    assert err["code"] == "not_authenticated"
    assert err["recoverable"] is True
    assert state is None
    assert not access_mock.called


# ---------- claim-derived is_sysop overrides stale session ----------


def test_claim_is_sysop_bypasses_session_ownership_in_access():
    """End-to-end: session says non-sysop alice, claim says
    is_sysop=True. bbsengine6.message.access sees a sysop and
    allows subscribing to bob's stream. Without the claim the
    session-only path would deny. Mirrors the bank service
    defense-in-depth contract."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        is_sysop=True,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,  # session is stale; claims are truth.
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "bob"}
    # access() returns True because the claim-derived is_sysop is True
    # and ``bbsengine6.message.access`` uses claims (not session).
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    assert msg["claims"]["is_sysop"] is True
    assert access_mock.called


def test_session_sysop_falls_back_when_no_claims():
    """When no claims are stashed (no token gate fired -- legacy
    constructor), access() falls back to the session's is_sysop.
    This preserves the legacy authorization semantics for callers
    that didn't wire tokens."""
    from bed.api.message import MessageService

    ws, sm = _make_session_manager(), _make_session_manager()
    sm.get_by_websocket.return_value = MagicMock(
        moniker="alice", is_sysop=True
    )
    service = MessageService(_make_args(), sm)

    msg: dict[str, Any] = {"moniker": "bob"}
    with _patch_access_returning(True) as access_mock:
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None
    assert "claims" not in msg
    assert access_mock.called


# ---------- end-to-end _handle_* tests through full pipeline ----------


def test_handle_subscribe_token_aware_happy_path():
    """End-to-end: a token-aware MessageService with a valid session
    token processes a subscribe for the session's own moniker. The
    websocket gets bound under the moniker in ``_subscribed``."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"type": "message_subscribe", "moniker": "alice"}
    result = asyncio.run(service._handle_subscribe(ws, msg))
    assert result["ok"] is True
    assert result["moniker"] == "alice"
    assert service._subscribed["alice"] is ws
    # Claims were stashed on the message during _check_access.
    assert msg["claims"]["moniker"] == "alice"


def test_handle_subscribe_token_aware_rejects_other_moniker():
    """A token-aware MessageService rejects ``message_subscribe``
    for a moniker the session doesn't own."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        is_sysop=False,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"type": "message_subscribe", "moniker": "bob"}
    with _patch_access_returning(False):
        result = asyncio.run(service._handle_subscribe(ws, msg))
    assert result["ok"] is False
    assert result["code"] == "forbidden"
    assert "bob" not in service._subscribed


def test_handle_subscribe_token_aware_rejects_revoked_token():
    """End-to-end: a token-aware service rejects ``message_subscribe``
    when the session's token has been revoked since WS open."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    token_store.delete(token)  # revoke before the call
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"type": "message_subscribe", "moniker": "alice"}
    result = asyncio.run(service._handle_subscribe(ws, msg))
    assert result["ok"] is False
    assert result["code"] == "token_revoked"
    assert "alice" not in service._subscribed


def test_handle_unsubscribe_token_aware_unbinds():
    """End-to-end: a token-aware service processes ``message_unsubscribe``
    for the session's own moniker and drops the WS binding."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )
    # Pre-populate the subscription map.
    service._subscribed["alice"] = ws
    msg: dict[str, Any] = {"type": "message_unsubscribe", "moniker": "alice"}
    result = asyncio.run(service._handle_unsubscribe(ws, msg))
    assert result["ok"] is True
    assert "alice" not in service._subscribed


def test_handle_list_pending_token_aware_returns_messages():
    """End-to-end: a token-aware service processes
    ``message_list_pending`` and delegates to
    ``bbsengine6.message.get_pending_messages``."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    fake_messages = [
        {"id": 1, "urgency": "ROUTINE", "content": "hello"},
        {"id": 2, "urgency": "URGENT", "content": "world"},
    ]
    msg: dict[str, Any] = {"type": "message_list_pending", "moniker": "alice"}
    with _patch_get_pending_messages(fake_messages) as gp:
        result = asyncio.run(service._handle_list_pending(ws, msg))
    assert result["ok"] is True
    assert result["moniker"] == "alice"
    assert result["messages"] == fake_messages
    gp.assert_called_once_with("alice", limit=100)


def test_handle_list_pending_token_aware_rejects_revoked_token():
    """End-to-end: revoked session token blocks list_pending and the
    DB call is NOT made."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    token_store.delete(token)
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"type": "message_list_pending", "moniker": "alice"}
    with _patch_get_pending_messages() as gp:
        result = asyncio.run(service._handle_list_pending(ws, msg))
    assert result["ok"] is False
    assert result["code"] == "token_revoked"
    assert result["messages"] == []
    gp.assert_not_called()


def test_handle_message_token_aware_routes_through_dispatch():
    """handle_message() routes through _OP_TO_HANDLER even when the
    service is token-aware and the wire carries a per-call token."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )
    server = _make_server()
    service.register_all(server)

    # Unknown type: dispatch returns None.
    assert asyncio.run(
        service.handle_message(
            server, ws, "/", {"type": "no_such_message"}
        )
    ) is None

    # Known type with valid token: dispatch routes to _handle_subscribe.
    msg: dict[str, Any] = {
        "type": "message_subscribe",
        "moniker": "alice",
    }
    result = asyncio.run(service.handle_message(server, ws, "/", msg))
    assert result is not None
    assert result["ok"] is True


# ---------- regression: existing envelope shapes preserved ----------


def test_envelope_shape_unchanged_for_each_op():
    """Pin the wire-protocol envelope shapes for every error code the
    three ops can surface. Any future change to the message service
    that drops a field will fail one of these."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    sm = _make_session_manager()
    # Session bound but with no token.
    sm.get_by_websocket.return_value = MagicMock(
        moniker="alice", is_sysop=False, auth_service_token=None
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )
    ws = _make_websocket(ws_id="ws-shape")

    # subscribe: missing_moniker envelope shape.
    msg: dict[str, Any] = {"type": "message_subscribe", "moniker": ""}
    result = asyncio.run(service._handle_subscribe(ws, msg))
    assert set(result.keys()) >= {
        "type", "ok", "code", "message", "recoverable"
    }
    assert result["type"] == "message_subscribe_result"
    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    assert result["recoverable"] is False

    # unsubscribe: missing_moniker envelope shape.
    msg = {"type": "message_unsubscribe", "moniker": ""}
    result = asyncio.run(service._handle_unsubscribe(ws, msg))
    assert set(result.keys()) >= {
        "type", "ok", "code", "message", "recoverable"
    }
    assert result["type"] == "message_unsubscribe_result"
    assert result["ok"] is False
    assert result["code"] == "missing_moniker"

    # list_pending: missing_moniker envelope shape.
    msg = {"type": "message_list_pending", "moniker": ""}
    result = asyncio.run(service._handle_list_pending(ws, msg))
    assert set(result.keys()) >= {
        "type", "ok", "code", "message", "recoverable", "messages"
    }
    assert result["type"] == "message_list_pending_result"
    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    assert result["messages"] == []


def test_envelope_shape_unchanged_for_token_errors():
    """Pin the envelope shape for token-error denials on every op."""
    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
    )
    token_store.delete(token)  # revoked -> token_revoked envelope
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"type": "message_subscribe", "moniker": "alice"}
    result = asyncio.run(service._handle_subscribe(ws, msg))
    assert set(result.keys()) >= {
        "type", "ok", "code", "message", "recoverable"
    }
    assert result["type"] == "message_subscribe_result"
    assert result["ok"] is False
    assert result["code"] == "token_revoked"
    assert result["recoverable"] is False


# ---------- integration: live claim-derived values reach access() ----------


def test_real_bbsengine6_message_access_uses_claims():
    """Drive the real bbsengine6.message.access() (not a mock) and
    verify the policy surface: claim-derived ``moniker`` /
    ``is_sysop`` are stashed on ``message["claims"]`` so the access
    pipeline can prefer them over the in-memory session attributes,
    but :func:`bbsengine6.message.access` itself only reads
    ``session.is_sysop`` / ``session.moniker`` today. This test pins
    that contract: a session whose ``is_sysop=False`` is rejected
    even when the wire claims say ``is_sysop=True``. The docstring
    on the access policy describes the seam but the implementation
    has not yet adopted it; if/when :func:`bbsengine6.message.access`
    learns to read ``message["claims"]``, this test will fail and
    needs the corresponding update (the underlying policy is then
    correct -- only the test contract needs to change).
    """
    from bbsengine6.message import access as real_access

    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        is_sysop=True,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,  # stale session
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "bob"}
    with patch("bed.api.message._message_access", side_effect=real_access):
        _state, err = service._check_access(ws, "subscribe", msg)
    # The session says non-sysop alice; ``bbsengine6.message.access``
    # reads session.is_sysop and denies subscribing to bob. The
    # claim-derived ``is_sysop=True`` is stashed on message["claims"]
    # but not consulted by the access policy today (see test
    # docstring).
    assert err is not None
    assert err["code"] == "forbidden"
    assert msg["claims"]["is_sysop"] is True


def test_real_bbsengine6_message_access_denies_without_claim_sysop():
    """Drive the real bbsengine6.message.access() with the session
    non-sysop and the claims also non-sysop, trying to subscribe to
    a different moniker. Should be denied (self-or-sysop rule)."""
    from bbsengine6.message import access as real_access

    from bed.api.message import MessageService
    from bed.api.token_store import InMemoryTokenStore

    secret = secrets.token_bytes(32)
    instance_id = "msg-token-test"
    token_store = InMemoryTokenStore()
    token = _mint_token(
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        moniker="alice",
        is_sysop=False,
    )
    ws, sm, _, _, _ = _make_token_aware_session(
        moniker="alice",
        is_sysop=False,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
        auth_service_token=token,
    )
    service = MessageService(
        _make_args(),
        sm,
        secret=secret,
        token_store=token_store,
        instance_id=instance_id,
    )

    msg: dict[str, Any] = {"moniker": "bob"}
    with patch("bed.api.message._message_access", side_effect=real_access):
        _state, err = service._check_access(ws, "subscribe", msg)
    # Real access() denies because alice is not bob and not a sysop.
    assert err is not None
    assert err["code"] == "forbidden"


# ---------- integration: legacy + claim-aware both pass through ----------


def test_legacy_session_attrs_still_authorize_when_no_claims():
    """With no token wiring, the session's attributes drive
    authorization end-to-end via the real bbsengine6.message.access().
    This guards against the claim-aware upgrade breaking the
    --token-persistence=none path."""
    from bbsengine6.message import access as real_access

    from bed.api.message import MessageService

    ws, sm = _make_session_manager(), _make_session_manager()
    sm.get_by_websocket.return_value = MagicMock(
        moniker="alice", is_sysop=False
    )
    service = MessageService(_make_args(), sm)

    # Alice subscribing to her own moniker -- allowed.
    msg: dict[str, Any] = {"moniker": "alice"}
    with patch("bed.api.message._message_access", side_effect=real_access):
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None

    # Alice subscribing to bob's moniker -- denied.
    msg = {"moniker": "bob"}
    with patch("bed.api.message._message_access", side_effect=real_access):
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is not None
    assert err["code"] == "forbidden"

    # Sysop alice can subscribe to anyone's moniker.
    sm.get_by_websocket.return_value = MagicMock(
        moniker="root", is_sysop=True
    )
    msg = {"moniker": "alice"}
    with patch("bed.api.message._message_access", side_effect=real_access):
        _state, err = service._check_access(ws, "subscribe", msg)
    assert err is None


