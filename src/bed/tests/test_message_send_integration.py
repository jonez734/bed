"""message_send through the real gate chain, with no gate mocked.

This is the path that rejected every send with missing_moniker. The session lookup, the
wire-token check against the token store, the shape gate, and bbsengine6's access() all run
for real. Only the database write is replaced, so no live database is needed.

A member's send passes every gate and reaches the write with the session as the sender. A
member naming someone else is refused by the handler before the write.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from bed.api import message as message_mod
from test_message_service import (
    _make_args,
    _make_session_manager,
    _make_token_aware_session,
    _mint_token,
)
from bed.api.token_store import InMemoryTokenStore
import secrets


def _rig(moniker="alice", is_sysop=False):
    """A real token-aware service, a real session bound to a real token, and a stub DB write."""
    secret = secrets.token_bytes(32)
    store = InMemoryTokenStore()
    instance_id = "msg-token-integration"
    token = _mint_token(
        secret=secret, token_store=store, instance_id=instance_id,
        moniker=moniker, is_sysop=is_sysop,
    )
    ws, reg, _, _, _ = _make_token_aware_session(
        moniker=moniker, is_sysop=is_sysop, secret=secret, token_store=store,
        instance_id=instance_id, auth_service_token=token,
    )
    svc = message_mod.MessageService(
        _make_args(), reg, secret=secret, token_store=store, instance_id=instance_id,
    )
    cur = MagicMock()
    cur.fetchone.return_value = (7,)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    svc._message_conn = MagicMock(return_value=conn)
    return svc, ws, token, cur


def _send(svc, ws, message):
    return asyncio.run(svc.handle_message(None, ws, "/", message))


@pytest.mark.unit
def test_member_send_with_no_sender_passes_every_real_gate():
    svc, ws, token, cur = _rig("alice")
    reply = _send(svc, ws, {"type": "message_send", "token": token,
                            "recipients": ["bob"], "content": "hello from alice"})
    assert reply["ok"] is True, reply
    assert reply["message_id"] == 7
    cur.execute.assert_called_once()


@pytest.mark.unit
def test_member_send_with_their_own_sender_passes_every_real_gate():
    svc, ws, token, cur = _rig("alice")
    reply = _send(svc, ws, {"type": "message_send", "token": token, "sender": "alice",
                            "recipients": ["bob"], "content": "hi"})
    assert reply["ok"] is True, reply


@pytest.mark.unit
def test_member_naming_another_sender_is_refused_before_the_write():
    svc, ws, token, cur = _rig("alice")
    reply = _send(svc, ws, {"type": "message_send", "token": token, "sender": "carol",
                            "recipients": ["bob"], "content": "hi"})
    assert reply["ok"] is False
    assert reply["code"] == "forbidden"
    cur.execute.assert_not_called()


@pytest.mark.unit
def test_send_without_recipients_is_refused_by_the_shape_gate():
    svc, ws, token, cur = _rig("alice")
    reply = _send(svc, ws, {"type": "message_send", "token": token, "recipients": [], "content": "x"})
    assert reply["ok"] is False
    assert reply["code"] == "missing_recipients"
    cur.execute.assert_not_called()


@pytest.mark.unit
def test_sysop_send_with_a_chosen_sender_passes_every_real_gate():
    svc, ws, token, cur = _rig("ops", is_sysop=True)
    reply = _send(svc, ws, {"type": "message_send", "token": token, "sender": "alice",
                            "recipients": ["bob"], "content": "from the desk"})
    assert reply["ok"] is True, reply
    params = cur.execute.call_args.args[1]
    assert params[5] == "alice"


@pytest.mark.unit
def test_sender_is_filled_from_the_validated_session_before_authz():
    # The plain member send names no sender. The gate must fill it from the validated token,
    # so access() authorises the member as themselves.
    svc, ws, token, cur = _rig("alice")
    msg = {"type": "message_send", "token": token, "recipients": ["bob"], "content": "x"}
    state, err = svc._check_access(ws, "send", msg)
    assert err is None, err
    assert msg.get("sender") == "alice"
