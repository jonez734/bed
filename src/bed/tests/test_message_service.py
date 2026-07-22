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
