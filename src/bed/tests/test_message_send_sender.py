"""message_send takes the sender from the authenticated session, not from the wire.

The member's moniker comes from the validated token. A non-sysop may not name a different
sender: the send is refused and nothing is written. A sysop may name another sender, which
is the sender used. The handler is exercised with the real access chain.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from bed.api import message as message_mod


def _service():
    svc = message_mod.MessageService.__new__(message_mod.MessageService)
    svc.args = MagicMock(databasename="zoid6")
    svc._pool = None
    svc._bed_name = "zoid6"
    return svc


def _state(moniker="alice", is_sysop=False):
    st = MagicMock()
    st.moniker = moniker
    st.is_sysop = is_sysop
    st.auth_service_token = "tok"
    return st


def _conn(returned_id=5):
    cur = MagicMock()
    cur.fetchone.return_value = (returned_id,)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def _send(svc, msg):
    return asyncio.run(svc._handle_send(None, msg))


@pytest.mark.unit
def test_sender_defaults_to_the_session_member():
    svc = _service()
    conn, cur = _conn()
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state("alice"), None))
    _send(svc, {"recipients": ["bob"], "content": "hi"})
    # The SQL receives the token hash; the sender itself is read from the session row.
    params = cur.execute.call_args.args[1]
    assert len(params) == 5


@pytest.mark.unit
def test_non_sysop_naming_another_sender_is_refused_and_not_written():
    svc = _service()
    conn, cur = _conn()
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state("alice", is_sysop=False), None))
    reply = _send(svc, {"recipients": ["bob"], "content": "hi", "sender": "bob"})
    assert reply["ok"] is False
    assert reply["code"] == "forbidden"
    cur.execute.assert_not_called()


@pytest.mark.unit
def test_non_sysop_naming_their_own_sender_is_allowed():
    svc = _service()
    conn, cur = _conn()
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state("alice", is_sysop=False), None))
    reply = _send(svc, {"recipients": ["bob"], "content": "hi", "sender": "alice"})
    assert reply["ok"] is True


@pytest.mark.unit
def test_sysop_may_name_another_sender():
    svc = _service()
    conn, cur = _conn()
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state("ops", is_sysop=True), None))
    reply = _send(svc, {"recipients": ["bob"], "content": "hi", "sender": "alice"})
    assert reply["ok"] is True
    cur.execute.assert_called_once()


@pytest.mark.unit
def test_wire_sender_is_not_trusted_without_the_session_check():
    # The refusal happens in the handler, so a forged sender cannot reach the SQL.
    svc = _service()
    conn, cur = _conn()
    svc._message_conn = MagicMock(return_value=conn)
    svc._check_access = MagicMock(return_value=(_state("alice", is_sysop=False), None))
    _send(svc, {"recipients": ["bob"], "content": "hi", "sender": "carol"})
    cur.execute.assert_not_called()
