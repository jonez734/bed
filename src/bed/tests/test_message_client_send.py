"""BedMessageServiceClient.send_message sends a message_send op and returns the server reply."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bed.client import messageservice as ms
from bed.client.exceptions import BedUnavailable


def _client(reply=None, raises=None):
    conn = MagicMock(name="conn")
    if raises is not None:
        conn.send = AsyncMock(side_effect=raises)
    else:
        conn.send = AsyncMock(return_value=reply)
    client = ms.BedMessageServiceClient(conn)
    client._token = "tok-alice"
    client._owner = True
    return client, conn


@pytest.mark.unit
def test_send_message_sends_the_wire_op_with_token_and_fields():
    client, conn = _client(reply={"type": "message_send_result", "ok": True, "message_id": 7})
    asyncio.run(client.send_message(recipients=["bob"], content="hello", channel="member:direct", urgency="ROUTINE"))
    sent = conn.send.await_args.args[0]
    assert sent["type"] == "message_send"
    assert sent["token"] == "tok-alice"
    assert sent["recipients"] == ["bob"]
    assert sent["content"] == "hello"
    assert sent["channel"] == "member:direct"
    assert sent["urgency"] == "ROUTINE"


@pytest.mark.unit
def test_send_message_returns_the_server_reply():
    reply = {"type": "message_send_result", "ok": True, "message_id": 7}
    client, _ = _client(reply=reply)
    assert asyncio.run(client.send_message(recipients=["bob"], content="hi")) == reply


@pytest.mark.unit
def test_send_message_defaults_channel_and_urgency():
    client, conn = _client(reply={"ok": True})
    asyncio.run(client.send_message(recipients=["bob"], content="hi"))
    sent = conn.send.await_args.args[0]
    assert sent["channel"] == "member:direct"
    assert sent["urgency"] == "ROUTINE"


@pytest.mark.unit
def test_send_message_reports_bed_unavailable_without_raising():
    client, _ = _client(raises=BedUnavailable("connection refused"))
    reply = asyncio.run(client.send_message(recipients=["bob"], content="hi"))
    assert reply["ok"] is False
    assert reply["code"] == "bed_unavailable"


@pytest.mark.unit
def test_default_urgency_is_a_value_the_database_enum_accepts():
    # engine.notify_urgency_enum (bbsengine6 sql/message_enum.sql) accepts only these four.
    enum_values = {"ROUTINE", "IMPORTANT", "URGENT", "CRITICAL"}
    import inspect
    default = inspect.signature(ms.BedMessageServiceClient.send_message).parameters["urgency"].default
    assert default in enum_values
