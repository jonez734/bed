#!/usr/bin/env python3
# bed/tests/test_message_multisession.py
# A member with more than one open client receives each DM on every client,
# and unsubscribe removes only the calling client.

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/home/opencode/data/work/bed/src")


def _service():
    from bed.api.message import MessageService

    svc = MessageService(MagicMock(), MagicMock())
    svc.server = MagicMock()
    svc.server.send_to = AsyncMock()
    return svc


def _payload(recipient):
    return json.dumps({"recipient_moniker": recipient, "message_id": 1})


def _subscribe(svc, ws, moniker):
    with patch.object(svc, "_check_access", return_value=(MagicMock(moniker="x"), None)):
        return asyncio.run(
            svc._handle_subscribe(ws, {"type": "message_subscribe", "moniker": moniker})
        )


def test_both_sessions_receive_dm():
    svc = _service()
    ws1, ws2 = MagicMock(name="ws1"), MagicMock(name="ws2")
    _subscribe(svc, ws1, "alice")
    _subscribe(svc, ws2, "alice")

    asyncio.run(svc._dispatch_notification(_payload("alice")))

    sent_to = {c.args[0] for c in svc.server.send_to.await_args_list}
    assert sent_to == {ws1, ws2}


def test_unsubscribe_removes_only_calling_session():
    svc = _service()
    ws1, ws2 = MagicMock(name="ws1"), MagicMock(name="ws2")
    _subscribe(svc, ws1, "alice")
    _subscribe(svc, ws2, "alice")

    with patch.object(svc, "_check_access", return_value=(MagicMock(moniker="x"), None)):
        asyncio.run(
            svc._handle_unsubscribe(ws1, {"type": "message_unsubscribe", "moniker": "alice"})
        )

    asyncio.run(svc._dispatch_notification(_payload("alice")))
    sent_to = {c.args[0] for c in svc.server.send_to.await_args_list}
    assert sent_to == {ws2}


def test_dead_session_is_pruned_and_others_still_get_dm():
    svc = _service()
    dead, live = MagicMock(name="dead"), MagicMock(name="live")
    _subscribe(svc, dead, "alice")
    _subscribe(svc, live, "alice")

    async def send_to(ws, envelope):
        if ws is dead:
            raise ConnectionError("gone")

    svc.server.send_to = AsyncMock(side_effect=send_to)
    asyncio.run(svc._dispatch_notification(_payload("alice")))

    assert svc._sessions["alice"] == {live}
    assert svc._subscribed["alice"] is live


def test_legacy_subscribed_map_still_populated():
    svc = _service()
    ws = MagicMock()
    _subscribe(svc, ws, "bob")
    assert svc._subscribed["bob"] is ws
