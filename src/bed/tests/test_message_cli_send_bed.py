"""``message send`` goes through the bed daemon by default, and the database only with --direct.

Covers routing (send is no longer forced to direct), the auth requirement in bed mode, the
bed-mode handler (calls the send_message client and reports the id), and the unchanged
direct-mode path for ``--direct``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bed.tools import message as msg_tool


def _args(**kw):
    base = dict(subcommand="send", recipients=["bob"], content="hello", template=None,
                channel="member:direct", urgency="ROUTINE", direct=False, debug=False,
                moniker="alice", _backend="bed")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.unit
def test_send_is_not_forced_to_direct_mode():
    assert "send" not in msg_tool._DIRECT_ONLY_SUBCMDS


@pytest.mark.unit
def test_send_needs_auth_on_the_bed_backend():
    assert msg_tool._needs_auth("send") is True


@pytest.mark.unit
def test_backend_guard_allows_send_on_bed():
    assert msg_tool._backend_guard(_args(_backend="bed"), "send") is True


@pytest.mark.unit
def test_bed_mode_send_calls_the_daemon_and_reports_the_id(capsys):
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"ok": True, "message_id": 9, "channel": "member:direct"})
    with patch.object(msg_tool, "_bed_send_client", return_value=client), \
         patch.object(msg_tool, "_check_self_or_sysop", return_value=True):
        ok = msg_tool.message_send(_args(), "alice")
    assert ok is True
    kwargs = client.send_message.await_args.kwargs
    assert kwargs["recipients"] == ["bob"]
    assert kwargs["content"] == "hello"
    assert kwargs["urgency"] == "ROUTINE"
    assert kwargs["channel"] == "member:direct"


@pytest.mark.unit
def test_bed_mode_send_reports_a_refusal_as_failure():
    client = MagicMock()
    client.send_message = AsyncMock(return_value={"ok": False, "code": "forbidden", "message": "no"})
    with patch.object(msg_tool, "_bed_send_client", return_value=client), \
         patch.object(msg_tool, "_check_self_or_sysop", return_value=True):
        assert msg_tool.message_send(_args(), "alice") is False


@pytest.mark.unit
def test_direct_mode_send_still_uses_the_database_path():
    with patch.object(msg_tool, "_bed_send_client") as bed_client, \
         patch.object(msg_tool.message_module, "store_message_with_checks",
                      return_value={"message_id": 3, "recipients_stored": ["bob"], "recipients_blocked": [], "rate_limit_ok": True}) as store, \
         patch.object(msg_tool, "_check_self_or_sysop", return_value=True):
        ok = msg_tool.message_send(_args(_backend="direct", direct=True), "alice")
    assert ok is True
    store.assert_called_once()
    bed_client.assert_not_called()
