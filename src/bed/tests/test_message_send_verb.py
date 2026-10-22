"""The message_send wire verb sends through engine.bed_message_send, keyed by the token hash.

The sender is never taken from the message: the SQL reads it from the session row. These tests
pin the routing, the wire shape, the recipient and body checks, and the token-hash call.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from bed.api import message as message_mod
from bed.store.token_store import token_hash


def _service():
    args = MagicMock(databasename="zoid6")
    svc = message_mod.MessageService.__new__(message_mod.MessageService)
    svc.args = args
    svc._pool = None
    svc._bed_name = "zoid6"
    return svc


def _state(token="tok-alice"):
    st = MagicMock()
    st.moniker = "alice"
    st.is_sysop = False
    st.auth_service_token = token
    return st


def _conn(returned_id=42):
    cur = MagicMock()
    cur.fetchone.return_value = (returned_id,)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


@pytest.mark.unit
def test_send_is_a_routed_wire_type():
    assert message_mod._TYPE_TO_OP["message_send"] == "send"
    assert "message_send" in message_mod.MessageService.HANDLED_TYPES
    assert message_mod._OP_TO_HANDLER["send"] is message_mod.MessageService._handle_send


@pytest.mark.unit
def test_successful_send_returns_the_message_id():
    svc = _service()
    conn, cur = _conn(returned_id=42)
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state(), None))
    msg = {"type": "message_send", "sender": "alice", "recipients": ["bob"],
           "content": "hello", "channel": "member:direct", "urgency": "NORMAL"}
    reply = asyncio.run(svc._handle_send(None, msg))
    assert reply["type"] == "message_send_result"
    assert reply["ok"] is True
    assert reply["message_id"] == 42


@pytest.mark.unit
def test_send_passes_the_token_hash_not_a_sender_name():
    svc = _service()
    conn, cur = _conn()
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state("tok-alice"), None))
    msg = {"type": "message_send", "sender": "alice", "recipients": ["bob"],
           "content": "hi", "channel": "member:direct", "urgency": "NORMAL"}
    asyncio.run(svc._handle_send(None, msg))
    sql, params = cur.execute.call_args.args
    assert "engine.bed_message_send" in sql
    assert params[0] == token_hash("tok-alice")
    assert "alice" not in [str(p) for p in params[1:2]]


@pytest.mark.unit
def test_send_refused_without_recipients():
    svc = _service()
    svc._check_access = MagicMock(return_value=(_state(), None))
    msg = {"type": "message_send", "sender": "alice", "recipients": [],
           "content": "hi", "channel": "member:direct", "urgency": "NORMAL"}
    reply = asyncio.run(svc._handle_send(None, msg))
    assert reply["ok"] is False
    assert reply["code"] == "missing_recipients"


@pytest.mark.unit
def test_send_refused_without_content():
    svc = _service()
    svc._check_access = MagicMock(return_value=(_state(), None))
    msg = {"type": "message_send", "sender": "alice", "recipients": ["bob"],
           "content": "   ", "channel": "member:direct", "urgency": "NORMAL"}
    reply = asyncio.run(svc._handle_send(None, msg))
    assert reply["ok"] is False
    assert reply["code"] == "missing_content"


@pytest.mark.unit
def test_send_denied_by_access_gate_is_returned_as_forbidden():
    svc = _service()
    svc._check_access = MagicMock(return_value=(None, {"code": "forbidden", "message": "no", "recoverable": False}))
    msg = {"type": "message_send", "sender": "bob", "recipients": ["alice"],
           "content": "hi", "channel": "member:direct", "urgency": "NORMAL"}
    reply = asyncio.run(svc._handle_send(None, msg))
    assert reply["ok"] is False
    assert reply["code"] == "forbidden"


@pytest.mark.unit
def test_send_database_error_is_reported_not_raised():
    svc = _service()
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.side_effect = RuntimeError("no live session for this token")
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state(), None))
    msg = {"type": "message_send", "sender": "alice", "recipients": ["bob"],
           "content": "hi", "channel": "member:direct", "urgency": "NORMAL"}
    reply = asyncio.run(svc._handle_send(None, msg))
    assert reply["ok"] is False
    assert "no live session" in reply["message"]
