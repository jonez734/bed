"""One-shot bed CLI commands close their websocket when they finish.

Each entry point runs against a fake connection that records its close. The socket must be
closed on the loop that opened it, whether the command succeeds or fails, and nothing may be
left open for garbage collection to report.
"""

import asyncio
import gc
import os
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bed.tools import auth as auth_tool


class FakeConn:
    """Stands in for BedConnection: send() answers, and aclose() records the close."""

    def __init__(self, reply):
        self.reply = reply
        self.closes = 0
        self.close_loop = None
        self._ws = object()

    async def send(self, msg):
        return dict(self.reply)

    async def aclose(self):
        self.closes += 1
        self.close_loop = asyncio.get_running_loop()
        self._ws = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
        return False


def _args(tmp_path, **kw):
    tok = tmp_path / "bed.token"
    tok.write_text("tok-alice")
    os.chmod(tok, 0o600)
    base = dict(token_file=str(tok), token=None, moniker="alice", password="pw",
                bed_host="127.0.0.1", bed_port=8765, bed_path="/", bed_call_timeout=5.0,
                bed_probe_timeout=0.25, debug=False, account=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _run(entry, args, reply):
    fake = FakeConn(reply)
    with warnings.catch_warnings(record=True) as rec, \
         patch("bed.client.get_bed_connection", return_value=fake), \
         patch.object(auth_tool, "_persist_token", return_value=True):
        warnings.simplefilter("always")
        result = entry(args)
        gc.collect()
    return fake, result, [w for w in rec if issubclass(w.category, ResourceWarning)]


OK_REPLY = {"ok": True, "moniker": "alice", "token": "tok-new", "session_id": "s" * 16,
            "expires_at": "2099-01-01T00:00:00Z", "is_sysop": False}


@pytest.mark.unit
@pytest.mark.parametrize("entry", [auth_tool.auth_reconnect, auth_tool.auth_refresh, auth_tool.auth_revoke])
def test_token_command_closes_its_connection_on_success(tmp_path, entry):
    fake, _, _ = _run(entry, _args(tmp_path), OK_REPLY)
    assert fake.closes == 1


@pytest.mark.unit
@pytest.mark.parametrize("entry", [auth_tool.auth_reconnect, auth_tool.auth_refresh, auth_tool.auth_revoke])
def test_token_command_closes_its_connection_on_refusal(tmp_path, entry):
    fake, _, _ = _run(entry, _args(tmp_path), {"ok": False, "code": "token_expired", "message": "no"})
    assert fake.closes == 1


@pytest.mark.unit
@pytest.mark.parametrize("entry", [auth_tool.auth_reconnect, auth_tool.auth_refresh, auth_tool.auth_revoke])
def test_token_command_leaves_no_resource_warning(tmp_path, entry):
    _, _, leaks = _run(entry, _args(tmp_path), OK_REPLY)
    assert leaks == [], [str(w.message) for w in leaks]


@pytest.mark.unit
def test_login_closes_its_connection(tmp_path):
    fake, _, leaks = _run(auth_tool.auth_login, _args(tmp_path), OK_REPLY)
    assert fake.closes == 1
    assert leaks == []


@pytest.mark.unit
def test_close_runs_on_the_loop_of_the_command(tmp_path):
    fake, _, _ = _run(auth_tool.auth_reconnect, _args(tmp_path), OK_REPLY)
    assert fake.close_loop is not None


@pytest.mark.unit
def test_message_reconnect_closes_its_connection(tmp_path):
    from bed.tools import message as msg_tool
    fake = FakeConn(OK_REPLY)
    args = _args(tmp_path)
    with warnings.catch_warnings(record=True) as rec, \
         patch("bed.client.get_bed_connection", return_value=fake), \
         patch.object(msg_tool, "_token", create=True):
        warnings.simplefilter("always")
        msg_tool._authenticate_ws(args)
        gc.collect()
    assert fake.closes == 1
    assert [w for w in rec if issubclass(w.category, ResourceWarning)] == []


@pytest.mark.unit
def test_bank_wrapper_closes_its_connection_after_a_call():
    from bed.tools import bank as bank_tool
    fake = FakeConn({"ok": True, "moniker": "alice", "balance": 100, "account": True})
    with patch("bed.client.get_bed_connection", return_value=fake):
        w = bank_tool._BedBankFacade(SimpleNamespace(bed_host="127.0.0.1", bed_port=8765,
                                                    bed_path="/", bed_call_timeout=5.0, token="t"))
        w.get_balance("alice")
    assert fake.closes == 1
