#!/usr/bin/env python3
# bed/tests/test_message_client_unread.py
# The push handler recounts from the DB; it never bumps by delta.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bed.client import messageservice as ms


def _client_with_handler():
    conn = MagicMock(name="conn")
    conn.send = AsyncMock(return_value={"ok": True})
    conn.subscribe = AsyncMock()
    client = ms.BedMessageServiceClient(conn)
    client.list_pending = AsyncMock(return_value={"ok": True, "messages": []})
    asyncio.run(client.subscribe("alice"))
    handler = conn.subscribe.await_args.args[0]
    return client, handler


def _reset_after_subscribe(set_count):
    """subscribe() seeds the count from list_pending; ignore that call."""
    set_count.reset_mock()


@patch("bed.client.messageservice._exact_unread", return_value=3)
@patch("bbsengine6.message.set_local_unread_count")
def test_push_sets_exact_count_from_db(set_count, _exact):
    _client, handler = _client_with_handler()
    _reset_after_subscribe(set_count)
    handler({"type": "message", "recipient_moniker": "alice", "status": "pending"})
    set_count.assert_called_with("alice", 3)


@patch("bed.client.messageservice._exact_unread", return_value=2)
@patch("bbsengine6.message.bump_local_unread_count")
@patch("bbsengine6.message.set_local_unread_count")
def test_push_never_bumps_by_delta(set_count, bump, _exact):
    _client, handler = _client_with_handler()
    _reset_after_subscribe(set_count)
    for status in ("pending", "delivered", "read"):
        handler({"type": "message", "recipient_moniker": "alice", "status": status})
    bump.assert_not_called()
    assert set_count.call_count == 3


@patch("bed.client.messageservice._exact_unread", return_value=5)
@patch("bbsengine6.message.set_local_unread_count")
def test_other_moniker_ignored(set_count, _exact):
    _client, handler = _client_with_handler()
    _reset_after_subscribe(set_count)
    handler({"type": "message", "recipient_moniker": "bob", "status": "pending"})
    set_count.assert_not_called()


def test_exact_unread_returns_zero_on_failure():
    with patch("bbsengine6.message.get_unread_count", side_effect=RuntimeError("no db")):
        assert ms._exact_unread("alice") == 0
